"""
strategies/DCA_Snipe/markets/eth/bot.py
------------------
BTC 15-Minute UP/DOWN Bot for Polymarket.

PRICE FEED — WebSocket first, REST fallback:
  1. MarketStream (WSS) opens BEFORE monitoring begins
  2. Waits up to 10s for first book snapshot (confirms subscription)
  3. All price reads come from the in-memory WSS cache (sub-millisecond)
  4. If WSS is disconnected on a tick, falls back to REST GET /midpoint once

STOP LOSS BEHAVIOUR:
  ETH_STOP_LOSS=0.55       → fixed absolute price
  ETH_STOP_LOSS_OFFSET=0.05→ dynamic = avg_price - 0.05 (recalculates on DCA)
  ETH_STOP_LOSS=null  AND
  ETH_STOP_LOSS_OFFSET=null→ stop loss = avg_price (break-even protection)
                              updated immediately after every BET/DCA fill

ENTRY ARMING:
  Prices must dip BELOW ENTRY_PRICE at least once before a trigger is valid.
  Prevents false entries when the window opens with prices already above target.

.env variables:
    ETH_ENTRY_PRICE, ETH_AMOUNT_PER_BET, ETH_TAKE_PROFIT
    ETH_STOP_LOSS          (fixed price | null → break-even mode)
    ETH_STOP_LOSS_OFFSET   (dynamic offset | null)
    ETH_BET_STEP           (null | float)
    ETH_POLL_INTERVAL      (seconds between ticks, used for DCA/SL polling)

    BUY_ORDER_TYPE=FAK|FOK|GTC
    SELL_ORDER_TYPE=GTC
    GTC_TIMEOUT_SECONDS=null|60
    FOK_GTC_FALLBACK=true
"""

import os
import sys
import time
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# ── Load .env from project root ────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
load_dotenv(_ROOT / ".env")

# ── Imports from project root ──────────────────────────────────────────────────
sys.path.insert(0, str(_ROOT))
from order_executor import OrderExecutor
from market_stream  import MarketStream

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "[%(asctime)s][%(levelname)s] - %(message)s",
    datefmt = "%H:%M:%S",
)
log = logging.getLogger("ETH-15M")


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════

ENTRY_PRICE    = float(os.getenv("ETH_ENTRY_PRICE",    "0.70"))
AMOUNT_PER_BET = float(os.getenv("ETH_AMOUNT_PER_BET", "2.50"))
TAKE_PROFIT    = float(os.getenv("ETH_TAKE_PROFIT",    "0.95"))
POLL_INTERVAL  = float(os.getenv("ETH_POLL_INTERVAL",  "1.0"))

_bet_step_raw  = os.getenv("ETH_BET_STEP",          "null").strip().lower()
BET_STEP: Optional[float] = None if _bet_step_raw == "null" else float(_bet_step_raw)

_sl_raw        = os.getenv("ETH_STOP_LOSS",         "null").strip().lower()
STOP_LOSS: Optional[float] = None if _sl_raw == "null" else float(_sl_raw)

_sl_offset_raw = os.getenv("ETH_STOP_LOSS_OFFSET",  "null").strip().lower()
STOP_LOSS_OFFSET: Optional[float] = None if _sl_offset_raw == "null" else float(_sl_offset_raw)

# STOP_LOSS=null AND STOP_LOSS_OFFSET=null → break-even mode (SL = avg_price)
SL_BREAKEVEN_MODE = (STOP_LOSS is None) and (STOP_LOSS_OFFSET is None)

BUY_ORDER_TYPE  = (os.getenv("BUY_ORDER_TYPE")  or os.getenv("ORDER_TYPE", "FAK")).upper()
SELL_ORDER_TYPE = (os.getenv("SELL_ORDER_TYPE") or "GTC").upper()
GTC_TIMEOUT_RAW = os.getenv("GTC_TIMEOUT_SECONDS", "null").strip().lower()
GTC_TIMEOUT: Optional[int] = None if GTC_TIMEOUT_RAW == "null" else int(GTC_TIMEOUT_RAW)

# WSS connection timeout before falling back to REST
WSS_READY_TIMEOUT = float(os.getenv("WSS_READY_TIMEOUT", "10.0"))

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CHAIN_ID  = 137


# ══════════════════════════════════════════════════════════════════════════════
#  CLOB CLIENT
# ══════════════════════════════════════════════════════════════════════════════

def build_clob_client():
    from py_clob_client.client     import ClobClient
    from py_clob_client.clob_types import ApiCreds

    pk   = os.getenv("POLY_PRIVATE_KEY",    "")
    fund = os.getenv("FUNDER_ADDRESS",      "")
    sig  = int(os.getenv("SIGNATURE_TYPE",  "2"))
    key  = os.getenv("POLY_API_KEY",        "")
    sec  = os.getenv("POLY_API_SECRET",     "")
    pas  = os.getenv("POLY_API_PASSPHRASE", "")

    if not all([pk, fund, key, sec, pas]):
        log.error("Missing credentials in .env — run get_credentials.py first")
        sys.exit(1)

    creds  = ApiCreds(api_key=key, api_secret=sec, api_passphrase=pas)
    client = ClobClient(
        host=CLOB_HOST, key=pk, chain_id=CHAIN_ID,
        creds=creds, signature_type=sig, funder=fund,
    )
    client.set_api_creds(creds)
    return client


# ══════════════════════════════════════════════════════════════════════════════
#  MARKET DISCOVERY
# ══════════════════════════════════════════════════════════════════════════════

def get_current_window_timestamp() -> int:
    return (int(datetime.now(timezone.utc).timestamp()) // 900) * 900


def fetch_market(slug: str) -> Optional[dict]:
    try:
        resp = requests.get(f"{GAMMA_API}/markets", params={"slug": slug}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict) and data.get("slug") == slug:
            return data
    except Exception as exc:
        log.warning(f"Gamma API error for {slug}: {exc}")
    return None


def wait_for_active_market() -> dict:
    log.info("Searching for active ETH 15M market ...")
    while True:
        ts = get_current_window_timestamp()
        for candidate in [ts, ts + 900]:
            slug   = f"eth-updown-15m-{candidate}"
            market = fetch_market(slug)
            if market and market.get("active") and not market.get("closed"):
                log.info(f"Found market: {slug}")
                log.info(f"  End date : {market.get('endDate') or market.get('end_date_iso')}")
                return market
        log.info("No active market yet — retrying in 15s ...")
        time.sleep(15)


def parse_market_tokens(market: dict) -> dict:
    import json
    outcomes = market.get("outcomes",      "[]")
    prices   = market.get("outcomePrices", "[0.5,0.5]")
    tokens   = market.get("clobTokenIds") or market.get("clob_token_ids", "[]")

    outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
    prices   = [float(p) for p in (json.loads(prices) if isinstance(prices, str) else prices)]
    tokens   = json.loads(tokens)   if isinstance(tokens, str) else tokens

    result = {}
    for i, name in enumerate(outcomes):
        key = "UP" if name.lower() in ("up", "yes") else "DOWN"
        result[key] = {
            "token_id": tokens[i] if i < len(tokens) else None,
            "price":    prices[i] if i < len(prices) else 0.5,
        }
    return result


def get_market_end_time(market: dict) -> Optional[datetime]:
    for field in ("endDate", "end_date_iso", "closedTime"):
        val = market.get(field)
        if val:
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                continue
    return None


def get_tick_size_rest(client, token_id: str) -> float:
    try:
        resp = client.get_tick_size(token_id)
        return float(resp) if resp else 0.01
    except Exception:
        return 0.01


# ══════════════════════════════════════════════════════════════════════════════
#  PRICE FEED — WSS primary, REST fallback
# ══════════════════════════════════════════════════════════════════════════════

def fetch_midpoint_rest(token_id: str) -> Optional[float]:
    """REST fallback for when WSS is temporarily disconnected."""
    try:
        resp = requests.get(
            f"{CLOB_HOST}/midpoint", params={"token_id": token_id}, timeout=5
        )
        resp.raise_for_status()
        return float(resp.json()["mid"])
    except Exception:
        return None


def get_prices(stream: MarketStream, token_up: str, token_down: str) -> Optional[dict]:
    """
    Read current prices. WSS first, REST fallback per token if needed.
    Returns {"UP": float, "DOWN": float} or None if both fail.
    """
    up_price   = stream.get_midpoint(token_up)
    down_price = stream.get_midpoint(token_down)

    # REST fallback only when WSS has no data (disconnected or not yet ready)
    if up_price is None:
        up_price = fetch_midpoint_rest(token_up)
    if down_price is None:
        down_price = fetch_midpoint_rest(token_down)

    if up_price is None or down_price is None:
        return None
    return {"UP": up_price, "DOWN": down_price}


# ══════════════════════════════════════════════════════════════════════════════
#  BOT STATE
# ══════════════════════════════════════════════════════════════════════════════

class BotState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.side                : Optional[str]   = None
        self.token_id            : Optional[str]   = None
        self.entry_price         : float           = 0.0
        self.last_bet_price      : float           = 0.0
        self.avg_price           : float           = 0.0
        self.total_shares        : float           = 0.0
        self.total_spent         : float           = 0.0
        self.effective_stop_loss : Optional[float] = None
        self.bets_count          : int             = 0
        self.in_position         : bool            = False
        self.tp_order_id         : Optional[str]   = None
        self.sl_order_id         : Optional[str]   = None
        # Entry arming: must see price BELOW entry_price at least once
        self.entry_armed         : bool            = False

    def update_after_bet(self, bet_price: float, usdc_paid: float, shares_received: float):
        self.total_shares += shares_received
        self.total_spent  += usdc_paid
        self.avg_price     = self.total_spent / self.total_shares if self.total_shares else bet_price

        # ── Stop loss resolution priority ──────────────────────────────────
        # 1. STOP_LOSS_OFFSET set → dynamic: avg_price - offset
        # 2. STOP_LOSS set        → fixed absolute price
        # 3. Both null            → break-even: SL = avg_price (no loss possible)
        if STOP_LOSS_OFFSET is not None:
            self.effective_stop_loss = round(self.avg_price - STOP_LOSS_OFFSET, 4)
        elif STOP_LOSS is not None:
            self.effective_stop_loss = STOP_LOSS
        else:
            # Break-even mode: SL always equals current avg_price
            # Rounds DOWN by 1 tick to avoid immediate self-triggering
            self.effective_stop_loss = round(self.avg_price - 0.01, 4)

        self.last_bet_price = bet_price
        self.bets_count    += 1
        self.in_position    = True

    def summary(self) -> str:
        mode   = f"DCA STEP={BET_STEP}" if BET_STEP else "Single bet"
        sl_val = f"{self.effective_stop_loss:.4f}" if self.effective_stop_loss else "none"
        if STOP_LOSS_OFFSET:
            sl_lbl = "(dynamic)"
        elif SL_BREAKEVEN_MODE:
            sl_lbl = "(break-even)"
        elif STOP_LOSS:
            sl_lbl = "(fixed)"
        else:
            sl_lbl = ""
        tp_id = self.tp_order_id[:10] + "..." if self.tp_order_id else "none"
        sl_id = self.sl_order_id[:10] + "..." if self.sl_order_id else "none"
        return (
            f"Side={self.side}  Bets={self.bets_count}  "
            f"Shares={self.total_shares:.4f}  Spent=${self.total_spent:.2f}  "
            f"AvgP={self.avg_price:.4f}  SL={sl_val}{sl_lbl}  TP={TAKE_PROFIT}\n"
            f"  [{mode}]  TP_order={tp_id}  SL_order={sl_id}"
        )


def _parse_bet_result(resp: dict, fallback_price: float, fallback_usdc: float):
    try:
        shares = float(resp.get("takingAmount", 0))
        usdc   = float(resp.get("makingAmount", 0))
        shares = shares if shares > 0 else fallback_usdc / fallback_price
        usdc   = usdc   if usdc   > 0 else fallback_usdc
        return shares, usdc
    except Exception:
        return fallback_usdc / fallback_price, fallback_usdc


# ══════════════════════════════════════════════════════════════════════════════
#  BRACKET ORDERS
# ══════════════════════════════════════════════════════════════════════════════

def place_brackets(executor: OrderExecutor, state: BotState, tick_size: float):
    """Cancel existing bracket orders then place fresh TP + SL GTC orders."""
    for order_id in [state.tp_order_id, state.sl_order_id]:
        if order_id:
            try:
                executor.gtc_tracker.cancel(order_id, log)
            except Exception:
                pass
    state.tp_order_id = None
    state.sl_order_id = None

    sl_disp = f"{state.effective_stop_loss:.4f}" if state.effective_stop_loss else "none"
    sl_mode = " (break-even)" if SL_BREAKEVEN_MODE else ""
    log.info(f"  Placing bracket orders:  TP={TAKE_PROFIT}  SL={sl_disp}{sl_mode}")

    result = executor.place_sell_bracket(
        token_id     = state.token_id,
        total_shares = state.total_shares,
        tp_price     = TAKE_PROFIT,
        sl_price     = state.effective_stop_loss,
        tick_size    = tick_size,
    )

    state.tp_order_id = result.get("tp_order_id")
    state.sl_order_id = result.get("sl_order_id")

    if not state.tp_order_id:
        log.warning("  TP bracket order failed to place — will monitor manually")
    if state.effective_stop_loss and not state.sl_order_id:
        log.warning("  SL bracket order failed to place — will monitor manually")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW LOOP
# ══════════════════════════════════════════════════════════════════════════════

def run_window(market: dict, executor: OrderExecutor, state: BotState):
    tokens     = parse_market_tokens(market)
    end_time   = get_market_end_time(market)
    token_up   = tokens["UP"]["token_id"]
    token_down = tokens["DOWN"]["token_id"]

    # ── REST tick sizes (initial; WSS updates them live via tick_size_change) ──
    tick_up   = get_tick_size_rest(executor.client, token_up)
    tick_down = get_tick_size_rest(executor.client, token_down)

    # ── Config display ─────────────────────────────────────────────────────────
    if STOP_LOSS_OFFSET:
        sl_cfg = f"SL_OFFSET={STOP_LOSS_OFFSET}(dynamic)"
    elif SL_BREAKEVEN_MODE:
        sl_cfg = "SL=avg_price(break-even)"
    else:
        sl_cfg = f"SL={STOP_LOSS}(fixed)"

    mode_str  = f"DCA every {BET_STEP} pts" if BET_STEP else "Single bet (no DCA)"
    gtc_disp  = f"{GTC_TIMEOUT}s" if GTC_TIMEOUT else "none (rests until filled)"

    log.info("=" * 60)
    log.info(f"Window | Market ID: {market.get('id', '')}")
    log.info(f"UP   token : {token_up}")
    log.info(f"DOWN token : {token_down}")
    log.info(f"End time   : {end_time}")
    log.info(
        f"Config     : ENTRY={ENTRY_PRICE}  BET=${AMOUNT_PER_BET}  TP={TAKE_PROFIT}"
        f"  {sl_cfg}  BUY={BUY_ORDER_TYPE}  SELL={SELL_ORDER_TYPE}  {mode_str}"
    )
    log.info(f"GTC timeout: {gtc_disp}")
    log.info("=" * 60)

    # ── Open WSS channel BEFORE starting the monitoring loop ──────────────────
    log.info("[WSS] Opening market channel ...")
    stream = MarketStream(asset_ids=[token_up, token_down])
    stream.start()

    ready = stream.wait_ready(timeout=WSS_READY_TIMEOUT)
    if ready:
        log.info(
            f"[WSS] Connected — first book received. "
            f"UP={stream.get_midpoint(token_up)}  DOWN={stream.get_midpoint(token_down)}"
        )
        # Sync tick sizes from WSS if already updated
        ts_up   = stream.get_tick_size(token_up)
        ts_down = stream.get_tick_size(token_down)
        if ts_up   != tick_up:
            log.info(f"[WSS] Tick size UP   updated: {tick_up} → {ts_up}")
            tick_up   = ts_up
        if ts_down != tick_down:
            log.info(f"[WSS] Tick size DOWN updated: {tick_down} → {ts_down}")
            tick_down = ts_down
    else:
        log.warning(
            f"[WSS] Not ready after {WSS_READY_TIMEOUT}s — "
            "will use REST fallback until WSS syncs"
        )

    # ── Main loop ──────────────────────────────────────────────────────────────
    try:
        while True:
            # ── Window expiry ──────────────────────────────────────────────
            now = datetime.now(timezone.utc)
            if end_time and now >= end_time:
                log.info("Window closed — cancelling all open bracket orders.")
                executor.gtc_tracker.cancel_all(log)
                break

            time_left  = (end_time - now).total_seconds() if end_time else 999
            mins, secs = divmod(int(time_left), 60)

            # ── Sync tick size from WSS (may change near 0.04 / 0.96) ─────
            tick_up   = stream.get_tick_size(token_up)
            tick_down = stream.get_tick_size(token_down)

            # ── Price read — WSS primary, REST fallback ────────────────────
            prices = get_prices(stream, token_up, token_down)
            if prices is None:
                log.warning("Price fetch failed (WSS+REST) — skipping tick")
                time.sleep(POLL_INTERVAL)
                continue

            up_price   = prices["UP"]
            down_price = prices["DOWN"]

            # ── Tick display ───────────────────────────────────────────────
            if state.in_position:
                cp      = up_price if state.side == "UP" else down_price
                sl_disp = f"{state.effective_stop_loss:.4f}" if state.effective_stop_loss else "none"
                sl_mode = "(BE)" if SL_BREAKEVEN_MODE else ""
                log.info(
                    f"[{mins:02d}:{secs:02d}]  {state.side}={cp:.4f}"
                    f"  | AvgP={state.avg_price:.4f}  SL={sl_disp}{sl_mode}"
                    f"  TP={TAKE_PROFIT}  Shares={state.total_shares:.4f}"
                    f"  {'WSS' if stream.is_connected else 'REST'}"
                )
            elif state.entry_armed:
                log.info(
                    f"[{mins:02d}:{secs:02d}]  UP={up_price:.4f}  DOWN={down_price:.4f}"
                    f"  | Waiting for ENTRY={ENTRY_PRICE}"
                    f"  {'WSS' if stream.is_connected else 'REST'}"
                )

            # ══════════════════════════════════════════════════════════════
            #  PHASE 1 — Waiting for entry
            # ══════════════════════════════════════════════════════════════
            if not state.in_position:
                # ── Entry arming ───────────────────────────────────────────
                if not state.entry_armed:
                    if up_price < ENTRY_PRICE and down_price < ENTRY_PRICE:
                        state.entry_armed = True
                        log.info(
                            f"  Entry armed — prices below ENTRY={ENTRY_PRICE}"
                            f" (UP={up_price:.4f} DOWN={down_price:.4f})"
                        )
                    else:
                        log.info(
                            f"[{mins:02d}:{secs:02d}]  UP={up_price:.4f}  DOWN={down_price:.4f}"
                            f"  | Waiting for price to dip below ENTRY={ENTRY_PRICE} before arming"
                        )
                        time.sleep(POLL_INTERVAL)
                        continue

                # ── Entry trigger ──────────────────────────────────────────
                triggered_side  = None
                triggered_price = None
                triggered_tick  = 0.01

                if up_price >= ENTRY_PRICE:
                    triggered_side, triggered_price, triggered_tick = "UP",   up_price,   tick_up
                elif down_price >= ENTRY_PRICE:
                    triggered_side, triggered_price, triggered_tick = "DOWN", down_price, tick_down

                if triggered_side:
                    log.info(
                        f"*** ENTRY TRIGGER: {triggered_side} reached {triggered_price:.4f}"
                        f" (target={ENTRY_PRICE}) ***"
                    )
                    state.side        = triggered_side
                    state.token_id    = token_up if triggered_side == "UP" else token_down
                    state.entry_price = triggered_price

                    resp = executor.place_buy(
                        token_id  = state.token_id,
                        price     = triggered_price,
                        usdc_size = AMOUNT_PER_BET,
                        tick_size = triggered_tick,
                    )

                    if resp and resp.get("success"):
                        shares, usdc_paid = _parse_bet_result(resp, triggered_price, AMOUNT_PER_BET)
                        log.info(f"BET #1 placed | shares={shares:.4f} | usdc=${usdc_paid:.2f} | resp={resp}")
                        state.update_after_bet(triggered_price, usdc_paid, shares)
                        place_brackets(executor, state, triggered_tick)
                        log.info(f"State: {state.summary()}")
                    else:
                        log.error(f"BET #1 failed — resetting | resp={resp}")
                        state.reset()

            # ══════════════════════════════════════════════════════════════
            #  PHASE 2 — In position
            #  Bracket orders fill automatically on-chain.
            #  Loop here handles: fallback TP/SL + DCA + break-even SL update
            # ══════════════════════════════════════════════════════════════
            else:
                cp        = up_price if state.side == "UP" else down_price
                tick_size = tick_up  if state.side == "UP" else tick_down

                # ── Break-even SL update ────────────────────────────────────
                # If SL = avg_price mode: update SL bracket every time avg moves
                # (happens after DCA). If SL order is placed it was already
                # replaced in place_brackets() after the DCA fill.
                # Nothing extra needed here — update_after_bet() handles it.

                # ── Safety fallback: TP ────────────────────────────────────
                if not state.tp_order_id and cp >= TAKE_PROFIT:
                    log.info(
                        f"*** TAKE PROFIT (manual fallback): {state.side} at {cp:.4f}"
                        f" >= {TAKE_PROFIT} — SELLING ALL ***"
                    )
                    executor.gtc_tracker.cancel_all(log)
                    resp = executor.place_sell_immediate(
                        token_id      = state.token_id,
                        total_shares  = state.total_shares,
                        current_price = cp,
                        tick_size     = tick_size,
                    )
                    if resp:
                        pnl = (cp - state.avg_price) * state.total_shares
                        log.info(f"POSITION CLOSED (TP fallback) | Est. P&L: +${pnl:.2f}")
                        break
                    else:
                        log.error("SELL failed on TP fallback — retrying next tick")
                        time.sleep(POLL_INTERVAL)
                        continue

                # ── Safety fallback: SL ────────────────────────────────────
                if (
                    not state.sl_order_id
                    and state.effective_stop_loss is not None
                    and cp <= state.effective_stop_loss
                ):
                    if SL_BREAKEVEN_MODE:
                        sl_label = "(break-even fallback)"
                    elif STOP_LOSS_OFFSET:
                        sl_label = "(dynamic fallback)"
                    else:
                        sl_label = "(fixed fallback)"
                    log.info(
                        f"*** STOP LOSS {sl_label}: {state.side} at {cp:.4f}"
                        f" <= {state.effective_stop_loss:.4f} — SELLING ALL ***"
                    )
                    executor.gtc_tracker.cancel_all(log)
                    resp = executor.place_sell_immediate(
                        token_id      = state.token_id,
                        total_shares  = state.total_shares,
                        current_price = cp,
                        tick_size     = tick_size,
                    )
                    if resp:
                        pnl = (cp - state.avg_price) * state.total_shares
                        log.info(f"POSITION CLOSED (SL fallback) | Est. P&L: ${pnl:.2f}")
                        break
                    else:
                        log.error("SELL failed on SL fallback — retrying next tick")
                        time.sleep(POLL_INTERVAL)
                        continue

                # ── DCA ───────────────────────────────────────────────────
                if BET_STEP is not None:
                    next_bet_price = round(state.last_bet_price + BET_STEP, 4)
                    if cp >= next_bet_price:
                        log.info(
                            f"*** DCA BET #{state.bets_count + 1}: {state.side} at {cp:.4f}"
                            f" >= {next_bet_price:.4f} ***"
                        )
                        resp = executor.place_buy(
                            token_id  = state.token_id,
                            price     = cp,
                            usdc_size = AMOUNT_PER_BET,
                            tick_size = tick_size,
                        )
                        if resp and resp.get("success"):
                            shares, usdc_paid = _parse_bet_result(resp, cp, AMOUNT_PER_BET)
                            log.info(f"DCA placed | shares={shares:.4f} | usdc=${usdc_paid:.2f}")
                            state.update_after_bet(cp, usdc_paid, shares)
                            # Replaces brackets with new totals + updated SL
                            place_brackets(executor, state, tick_size)
                            log.info(f"State: {state.summary()}")
                        else:
                            log.error(f"DCA failed — retrying next tick | resp={resp}")

            time.sleep(POLL_INTERVAL)

    finally:
        # Always close WSS cleanly when the window ends (normally or on exception)
        log.info("[WSS] Closing market channel.")
        stream.stop()

    log.info("Window loop ended.")


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def run():
    if STOP_LOSS_OFFSET:
        sl_startup = f"SL_OFFSET={STOP_LOSS_OFFSET}(dynamic)"
    elif SL_BREAKEVEN_MODE:
        sl_startup = "SL=avg_price(break-even)"
    else:
        sl_startup = f"SL={STOP_LOSS}(fixed)"

    gtc_disp = f"{GTC_TIMEOUT}s" if GTC_TIMEOUT else "none"
    log.info("ETH 15M Bot starting ...")
    log.info(
        f"Config: ENTRY={ENTRY_PRICE}  BET=${AMOUNT_PER_BET}  TP={TAKE_PROFIT}"
        f"  {sl_startup}  BET_STEP={BET_STEP}"
        f"  BUY={BUY_ORDER_TYPE}  SELL={SELL_ORDER_TYPE}  GTC_TIMEOUT={gtc_disp}"
    )

    client   = build_clob_client()
    executor = OrderExecutor(client=client, log=log)
    log.info("CLOB client authenticated OK")

    while True:
        state  = BotState()
        market = wait_for_active_market()
        run_window(market, executor, state)

        end_time  = get_market_end_time(market)
        wait_secs = max(5, (end_time - datetime.now(timezone.utc)).total_seconds() + 5) if end_time else 30
        log.info(f"Waiting {wait_secs:.0f}s for next window ...")
        time.sleep(wait_secs)


if __name__ == "__main__":
    run()