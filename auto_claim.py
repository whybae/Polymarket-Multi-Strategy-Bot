"""
auto_claim.py
-------------
Monitorea el proxy wallet y redime posiciones ganadas en Polymarket
enviando las transacciones al Relayer oficial.

FLUJO:
  1. GET data-api.polymarket.com/positions → filtra redeemable=true
  2. Para cada posición:
     a. Encode calldata: CTF.redeemPositions(USDC, bytes32(0), conditionId, [1,2])
     b. Firmar el hash del calldata con EOA (signatureType=0 — EOA directo)
     c. POST https://relayer-v2.polymarket.com/submit

PAYLOAD (signatureType=0 / type="EOA"):
  {
    "data":          "0x..."   calldata de redeemPositions
    "from":          "0x..."   EOA address (mismo que firma)
    "metadata":      ""
    "nonce":         "0"
    "proxyWallet":   "0x..."   FUNDER_ADDRESS
    "signature":     "0x..."   eth_sign del hash del calldata
    "to":            "0x..."   CTF contract
    "type":          "EOA"
  }

CONTRATOS (Polygon mainnet):
  CTF:         0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
  NegRisk:     0xd91e80cf2e7be2e162c6513ced06f1dd0da35296
  USDC.e:      0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174

.env variables requeridas:
  POLY_PRIVATE_KEY      EOA signer key (0x...)
  FUNDER_ADDRESS        proxy wallet address
  POLY_RPC              Polygon RPC URL
  CLAIM_CHECK_INTERVAL  segundos entre checks (default 180)
"""

import logging
import os
import time
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")

logging.basicConfig(
    level   = logging.INFO,
    format  = "[%(asctime)s][%(levelname)s][AutoClaim] - %(message)s",
    datefmt = "%H:%M:%S",
)
log = logging.getLogger("AutoClaim")

# ── Config ─────────────────────────────────────────────────────────────────────
CHECK_INTERVAL = int(os.getenv("CLAIM_CHECK_INTERVAL", "180"))
DATA_API       = "https://data-api.polymarket.com"
RELAYER_URL    = "https://relayer-v2.polymarket.com/submit"
CHAIN_ID       = 137

# ── Contracts ──────────────────────────────────────────────────────────────────
CTF_ADDRESS      = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADDRESS = "0xd91e80cf2e7be2e162c6513ced06f1dd0da35296"
USDC_ADDRESS     = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ADDR_ZERO        = "0x0000000000000000000000000000000000000000"

# ── ABIs ───────────────────────────────────────────────────────────────────────
CTF_ABI = [{"name":"redeemPositions","type":"function","inputs":[
    {"name":"collateralToken","type":"address"},
    {"name":"parentCollectionId","type":"bytes32"},
    {"name":"conditionId","type":"bytes32"},
    {"name":"indexSets","type":"uint256[]"},
],"outputs":[],"stateMutability":"nonpayable"}]

NEG_RISK_ABI = [{"name":"redeemPositions","type":"function","inputs":[
    {"name":"conditionId","type":"bytes32"},
    {"name":"amounts","type":"uint256[]"},
],"outputs":[],"stateMutability":"nonpayable"}]

# ── Web3 ───────────────────────────────────────────────────────────────────────
try:
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware
    from eth_account import Account
    from eth_account.messages import encode_defunct
except ImportError:
    raise ImportError("pip install web3 --break-system-packages")


def build_web3() -> Web3:
    rpc = os.getenv("POLY_RPC", "https://polygon-rpc.com").strip()
    w3  = Web3(Web3.HTTPProvider(rpc))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        raise ConnectionError(f"Cannot connect to RPC: {rpc}")
    return w3


# ══════════════════════════════════════════════════════════════════════════════
#  CALLDATA ENCODING
# ══════════════════════════════════════════════════════════════════════════════

def encode_redeem_calldata(w3: Web3, condition_id_hex: str) -> str:
    """CTF.redeemPositions(USDC, bytes32(0), conditionId, [1, 2])"""
    ctf      = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
    cid      = bytes.fromhex(condition_id_hex.removeprefix("0x"))
    calldata = ctf.encode_abi(
        "redeemPositions",
        args=[Web3.to_checksum_address(USDC_ADDRESS), b'\x00' * 32, cid, [1, 2]],
    )
    raw = calldata if isinstance(calldata, str) else calldata.hex()
    return "0x" + raw.removeprefix("0x")


def encode_neg_risk_calldata(w3: Web3, condition_id_hex: str, size_usdc: float) -> str:
    """NegRiskAdapter.redeemPositions(conditionId, [amount, amount])"""
    neg      = w3.eth.contract(address=Web3.to_checksum_address(NEG_RISK_ADDRESS), abi=NEG_RISK_ABI)
    cid      = bytes.fromhex(condition_id_hex.removeprefix("0x"))
    amount   = int(size_usdc * 1_000_000)
    calldata = neg.encode_abi("redeemPositions", args=[cid, [amount, amount]])
    raw = calldata if isinstance(calldata, str) else calldata.hex()
    return "0x" + raw.removeprefix("0x")


# ══════════════════════════════════════════════════════════════════════════════
#  SIGNING — signatureType=0 (EOA directo, eth_sign)
# ══════════════════════════════════════════════════════════════════════════════

def sign_calldata(private_key: str, data_hex: str) -> str:
    """
    signatureType=0: firma el keccak256 del calldata con eth_sign.
    eth_sign prefix: "\x19Ethereum Signed Message:\n32" + hash
    """
    data_bytes = bytes.fromhex(data_hex.removeprefix("0x"))
    msg_hash   = Web3.keccak(data_bytes)
    signable   = encode_defunct(primitive=msg_hash)
    signed     = Account.from_key(private_key).sign_message(signable)
    sig_hex    = signed.signature.hex()
    return sig_hex if sig_hex.startswith("0x") else "0x" + sig_hex


# ══════════════════════════════════════════════════════════════════════════════
#  RELAYER SUBMIT
# ══════════════════════════════════════════════════════════════════════════════

def submit_to_relayer(eoa_address: str, proxy_wallet: str, to: str,
                      data_hex: str, nonce: int, signature: str) -> dict | None:
    """
    POST https://relayer-v2.polymarket.com/submit
    type=EOA para signatureType=0 — sin signatureParams Safe.
    """
    payload = {
        "data"       : data_hex,
        "from"       : Web3.to_checksum_address(eoa_address),
        "metadata"   : "",
        "nonce"      : str(nonce),
        "proxyWallet": Web3.to_checksum_address(proxy_wallet),
        "signature"  : signature,
        "to"         : Web3.to_checksum_address(to),
        "type"       : "EOA",
    }

    log.info(f"    Submitting to relayer | nonce={nonce} | type=EOA")
    log.info(f"      to={to}")
    log.info(f"      data={data_hex[:22]}...")
    log.info(f"      sig={signature[:22]}...")

    try:
        resp = requests.post(
            RELAYER_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code in (200, 201):
            result = resp.json()
            log.info(f"    ✔ Relayer accepted!")
            log.info(f"      transactionID   = {result.get('transactionID','')}")
            log.info(f"      transactionHash = {result.get('transactionHash','')}")
            log.info(f"      state           = {result.get('state','')}")
            return result
        else:
            log.error(f"    ✗ Relayer rejected: HTTP {resp.status_code} — {resp.text[:300]}")
            return None
    except Exception as exc:
        log.error(f"    ✗ Relayer request failed: {exc}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  POSITION DISCOVERY
# ══════════════════════════════════════════════════════════════════════════════

def fetch_redeemable_positions(wallet: str) -> list:
    try:
        resp = requests.get(
            f"{DATA_API}/positions",
            params={"user": wallet, "sizeThreshold": "0.01",
                    "limit": "100", "redeemable": "true"},
            timeout=15,
        )
        resp.raise_for_status()
        data      = resp.json()
        positions = data if isinstance(data, list) else data.get("positions", [])
        return [p for p in positions if p.get("redeemable") is True]
    except Exception as exc:
        log.warning(f"fetch_positions error: {exc}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
#  REDEEM SINGLE POSITION
# ══════════════════════════════════════════════════════════════════════════════

def redeem_position(w3, position, private_key, eoa_address, proxy_wallet) -> bool:
    condition_id = position.get("conditionId", "")
    neg_risk     = position.get("negativeRisk", False) or position.get("neg_risk", False)
    size         = float(position.get("size", 0))
    title        = (position.get("title") or "")[:55]
    outcome      = position.get("outcome", "")
    value        = float(position.get("currentValue", 0))

    log.info(f"  → [{title}]")
    log.info(f"    outcome={outcome}  size={size:.4f}  value=${value:.4f}  negRisk={neg_risk}")
    log.info(f"    conditionId={condition_id}")

    if not condition_id:
        log.error("    ✗ Missing conditionId"); return False

    try:
        # 1. Encode calldata
        if neg_risk:
            data_hex = encode_neg_risk_calldata(w3, condition_id, size)
            to       = NEG_RISK_ADDRESS
        else:
            data_hex = encode_redeem_calldata(w3, condition_id)
            to       = CTF_ADDRESS

        # 2. Sign with EOA (signatureType=0)
        signature = sign_calldata(private_key, data_hex)
        log.info(f"    signature={signature[:22]}...")

        # 3. Submit — nonce=0 para EOA (no es Safe nonce)
        result = submit_to_relayer(
            eoa_address  = eoa_address,
            proxy_wallet = proxy_wallet,
            to           = to,
            data_hex     = data_hex,
            nonce        = 0,
            signature    = signature,
        )
        return result is not None

    except Exception as exc:
        log.error(f"    ✗ Error: {exc}", exc_info=True)
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════

def run_check_cycle(wallet, w3, private_key, eoa_address, already_claimed):
    log.info(f"Checking {wallet[:10]}... for redeemable positions")
    positions = fetch_redeemable_positions(wallet)

    if not positions:
        log.info("  No redeemable positions found."); return

    pending = [p for p in positions if p.get("conditionId") not in already_claimed]
    if not pending:
        log.info(f"  {len(positions)} redeemable — all already processed this session."); return

    log.info(f"  *** {len(pending)} position(s) to redeem! ***")
    for pos in pending:
        cid     = pos.get("conditionId")
        success = redeem_position(w3, pos, private_key, eoa_address, wallet)
        if success:
            already_claimed.add(cid)
        else:
            log.warning(f"    Will retry: {(cid or 'unknown')[:16]}...")


def run():
    private_key  = os.getenv("POLY_PRIVATE_KEY", "").strip()
    proxy_wallet = os.getenv("FUNDER_ADDRESS",   "").strip()

    if not private_key:
        log.error("POLY_PRIVATE_KEY not set in .env"); return
    if not proxy_wallet:
        log.error("FUNDER_ADDRESS not set in .env"); return

    try:
        w3 = build_web3()
        log.info(f"  Polygon connected | block={w3.eth.block_number}")
    except Exception as exc:
        log.error(f"Web3 connection failed: {exc}"); return

    try:
        account     = Account.from_key(private_key)
        eoa_address = account.address
        matic       = w3.from_wei(w3.eth.get_balance(eoa_address), "ether")
        log.info(f"  EOA signer    : {eoa_address}")
        log.info(f"  MATIC balance : {matic:.4f} POL")
        if matic < 0.001:
            log.warning("⚠️  Muy bajo balance MATIC")
    except Exception as exc:
        log.error(f"Invalid POLY_PRIVATE_KEY: {exc}"); return

    log.info("=" * 60)
    log.info("AutoClaim starting")
    log.info(f"  Proxy wallet   : {proxy_wallet}")
    log.info(f"  EOA signer     : {eoa_address}")
    log.info(f"  Signature type : 0 (EOA directo)")
    log.info(f"  Relayer        : {RELAYER_URL}")
    log.info(f"  Check interval : every {CHECK_INTERVAL}s ({CHECK_INTERVAL//60}m {CHECK_INTERVAL%60}s)")
    log.info("=" * 60)

    already_claimed: set = set()
    while True:
        try:
            run_check_cycle(
                wallet=proxy_wallet, w3=w3, private_key=private_key,
                eoa_address=eoa_address, already_claimed=already_claimed,
            )
        except Exception as exc:
            log.error(f"Check cycle error: {exc}")
        log.info(f"Next check in {CHECK_INTERVAL}s ...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()