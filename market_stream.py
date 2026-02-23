"""
market_stream.py
----------------
WebSocket price feed for Polymarket CLOB market channel.

Replaces the REST polling loop (fetch_midpoint every N seconds) with a
persistent WSS connection that receives pushed updates in real time.

WSS endpoint: wss://ws-subscriptions-clob.polymarket.com/ws/market
No authentication required for the market channel.

Message types handled:
  book            — full order book snapshot (sent on subscribe + after fills)
  price_change    — order placed/cancelled; contains best_bid / best_ask
  last_trade_price— trade executed; contains last execution price
  tick_size_change— tick size updated (price near 0.04 or 0.96)

Price derivation (follows Polymarket display logic):
  spread = best_ask - best_bid
  if spread <= 0.02:  midpoint = (best_bid + best_ask) / 2  ← tight book
  else:               midpoint = last_trade_price            ← wide spread

Benefits vs REST polling:
  - Latency: ~5-50ms push vs 500-2000ms poll round-trip
  - No rate-limit risk from repeated GET /midpoint calls
  - Tick size changes delivered immediately (important for 0.001 tick markets)
  - Orderbook depth available for smarter entry sizing
"""

import json
import logging
import threading
import time
from typing import Callable, Dict, Optional

try:
    import websocket  # websocket-client
except ImportError:
    import os
    os.system("pip install websocket-client --break-system-packages -q")
    import websocket

log = logging.getLogger("MarketStream")

WSS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PING_INTERVAL   = 20   # seconds between keep-alive pings
RECONNECT_DELAY = 3    # seconds before reconnect attempt
MAX_RECONNECT   = 10   # max consecutive reconnect attempts before giving up


class TokenPrice:
    """Thread-safe price state for one token."""

    def __init__(self):
        self._lock      = threading.Lock()
        self.best_bid   : Optional[float] = None
        self.best_ask   : Optional[float] = None
        self.last_trade : Optional[float] = None
        self.tick_size  : float           = 0.01
        self.timestamp  : int             = 0

    @property
    def midpoint(self) -> Optional[float]:
        """
        Polymarket midpoint logic:
          tight spread (≤0.02) → arithmetic midpoint of bid/ask
          wide spread           → last trade price
        Falls back gracefully if data is incomplete.
        """
        with self._lock:
            if self.best_bid is not None and self.best_ask is not None:
                spread = self.best_ask - self.best_bid
                if spread <= 0.02:
                    return round((self.best_bid + self.best_ask) / 2, 4)
            if self.last_trade is not None:
                return self.last_trade
            if self.best_bid is not None and self.best_ask is not None:
                return round((self.best_bid + self.best_ask) / 2, 4)
            return None

    def update_from_price_change(self, change: dict):
        with self._lock:
            bid = change.get("best_bid")
            ask = change.get("best_ask")
            if bid is not None:
                try:    self.best_bid = float(bid)
                except: pass
            if ask is not None:
                try:    self.best_ask = float(ask)
                except: pass

    def update_from_book(self, bids: list, asks: list):
        with self._lock:
            if bids:
                try:    self.best_bid = float(bids[0]["price"])
                except: pass
            if asks:
                try:    self.best_ask = float(asks[0]["price"])
                except: pass

    def update_last_trade(self, price: float):
        with self._lock:
            self.last_trade = price

    def update_tick_size(self, new_tick: float):
        with self._lock:
            self.tick_size = new_tick


class MarketStream:
    """
    Persistent WebSocket connection to Polymarket market channel.

    Usage:
        stream = MarketStream([token_up_id, token_down_id])
        stream.start()
        # wait for connection
        stream.wait_ready(timeout=10)
        # read prices
        mid = stream.get_midpoint(token_id)
        # stop when done
        stream.stop()

    Callbacks (optional):
        on_price_update(token_id, midpoint)  — called on every price change
    """

    def __init__(
        self,
        asset_ids: list[str],
        on_price_update: Optional[Callable[[str, float], None]] = None,
    ):
        self.asset_ids      = asset_ids
        self.on_price_update = on_price_update

        self._prices: Dict[str, TokenPrice] = {aid: TokenPrice() for aid in asset_ids}
        self._ws             : Optional[websocket.WebSocketApp] = None
        self._thread         : Optional[threading.Thread] = None
        self._ready          = threading.Event()    # set when first book received
        self._stop_flag      = threading.Event()
        self._reconnects     = 0
        self._connected      = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        """Start the WSS connection in a background thread."""
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="MarketStream")
        self._thread.start()

    def stop(self):
        """Disconnect and stop the background thread."""
        self._stop_flag.set()
        if self._ws:
            self._ws.close()

    def wait_ready(self, timeout: float = 15.0) -> bool:
        """
        Block until the first book snapshot arrives (confirms subscription).
        Returns True if ready, False if timed out.
        """
        return self._ready.wait(timeout=timeout)

    def get_midpoint(self, token_id: str) -> Optional[float]:
        return self._prices[token_id].midpoint if token_id in self._prices else None

    def get_tick_size(self, token_id: str) -> float:
        return self._prices[token_id].tick_size if token_id in self._prices else 0.01

    def get_prices(self) -> Dict[str, Optional[float]]:
        return {tid: tp.midpoint for tid, tp in self._prices.items()}

    def add_tokens(self, token_ids: list[str]):
        """Subscribe to additional tokens without reconnecting."""
        new = [t for t in token_ids if t not in self._prices]
        if not new:
            return
        for t in new:
            self._prices[t] = TokenPrice()
        self.asset_ids.extend(new)
        if self._ws and self._connected:
            msg = {"assets_ids": new, "operation": "subscribe"}
            try:
                self._ws.send(json.dumps(msg))
            except Exception as exc:
                log.warning(f"[WS] add_tokens send failed: {exc}")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Internal ───────────────────────────────────────────────────────────────

    def _run_loop(self):
        """Reconnect loop — runs until stop() is called."""
        while not self._stop_flag.is_set():
            try:
                self._connect()
            except Exception as exc:
                log.error(f"[WS] Connection error: {exc}")

            if self._stop_flag.is_set():
                break

            self._reconnects += 1
            if self._reconnects > MAX_RECONNECT:
                log.error(f"[WS] Max reconnect attempts ({MAX_RECONNECT}) reached.")
                break

            delay = min(RECONNECT_DELAY * self._reconnects, 30)
            log.info(f"[WS] Reconnecting in {delay}s (attempt {self._reconnects}) ...")
            self._stop_flag.wait(timeout=delay)

    def _connect(self):
        self._connected = False
        log.info(f"[WS] Connecting to {WSS_MARKET_URL} ...")

        self._ws = websocket.WebSocketApp(
            WSS_MARKET_URL,
            on_open    = self._on_open,
            on_message = self._on_message,
            on_error   = self._on_error,
            on_close   = self._on_close,
        )
        self._ws.run_forever(ping_interval=PING_INTERVAL, ping_timeout=10)

    def _on_open(self, ws):
        log.info("[WS] Connected — subscribing to market channel ...")
        self._connected = True
        self._reconnects = 0
        sub = {
            "assets_ids": self.asset_ids,
            "type"      : "market",
        }
        ws.send(json.dumps(sub))

    def _on_message(self, ws, raw: str):
        try:
            # Market channel may send a list of events
            data = json.loads(raw)
            events = data if isinstance(data, list) else [data]
            for event in events:
                self._dispatch(event)
        except Exception as exc:
            log.debug(f"[WS] Message parse error: {exc} | raw={raw[:200]}")

    def _dispatch(self, event: dict):
        etype = event.get("event_type")

        if etype == "book":
            asset_id = event.get("asset_id")
            if asset_id in self._prices:
                self._prices[asset_id].update_from_book(
                    event.get("bids", []),
                    event.get("asks", []),
                )
                self._ready.set()  # first book = subscription confirmed
                self._notify(asset_id)

        elif etype == "price_change":
            for change in event.get("price_changes", []):
                asset_id = change.get("asset_id")
                if asset_id in self._prices:
                    self._prices[asset_id].update_from_price_change(change)
                    self._notify(asset_id)

        elif etype == "last_trade_price":
            asset_id = event.get("asset_id")
            if asset_id in self._prices:
                try:
                    self._prices[asset_id].update_last_trade(float(event["price"]))
                    self._notify(asset_id)
                except (KeyError, ValueError):
                    pass

        elif etype == "tick_size_change":
            asset_id = event.get("asset_id")
            if asset_id in self._prices:
                try:
                    new_tick = float(event["new_tick_size"])
                    self._prices[asset_id].update_tick_size(new_tick)
                    log.info(f"[WS] Tick size changed for {asset_id[:16]}... → {new_tick}")
                except (KeyError, ValueError):
                    pass

        # best_bid_ask (behind custom_feature_enabled flag — handle if present)
        elif etype == "best_bid_ask":
            asset_id = event.get("asset_id")
            if asset_id in self._prices:
                tp = self._prices[asset_id]
                try:
                    tp._lock.acquire()
                    tp.best_bid = float(event["best_bid"])
                    tp.best_ask = float(event["best_ask"])
                finally:
                    tp._lock.release()
                self._notify(asset_id)

    def _notify(self, asset_id: str):
        if self.on_price_update:
            mid = self._prices[asset_id].midpoint
            if mid is not None:
                try:
                    self.on_price_update(asset_id, mid)
                except Exception as exc:
                    log.debug(f"[WS] on_price_update callback error: {exc}")

    def _on_error(self, ws, error):
        log.warning(f"[WS] Error: {error}")
        self._connected = False

    def _on_close(self, ws, code, reason):
        log.info(f"[WS] Closed (code={code} reason={reason})")
        self._connected = False