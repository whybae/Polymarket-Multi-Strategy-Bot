import os
import logging
import threading
import time
import requests
import json
import hmac
import hashlib
from pathlib import Path
from dotenv import dotenv_values
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from eth_account import Account
from eth_account.messages import encode_defunct

_ROOT = Path(__file__).resolve().parent
_CFG = dotenv_values(_ROOT / ".env")

def _cfg(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    if val is not None:
        return val.strip()
    return _CFG.get(key, default).strip()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(levelname)s][AutoClaim] - %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("AutoClaim")

# --- Config ---
CHECK_INTERVAL = int(_cfg("CLAIM_CHECK_INTERVAL", "60"))
CLAIM_METHOD = _cfg("CLAIM_METHOD", "relayer").lower()
CHECK_REAL_TIME = _cfg("CHECK_REAL_TIME", "false").lower() in ("true", "1", "yes")
DATA_API = "https://data-api.polymarket.com"
RELAYER_URL = "https://relayer-v2.polymarket.com/submit"
CHAIN_ID = 137
TX_DELAY_SECONDS = 2
RESOLVED_HIGH = 0.99
RESOLVED_LOW = 0.01
ZERO_THRESHOLD = 0.0001
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADDRESS = "0xd91e80cf2e7be2e162c6513ced06f1dd0da35296"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

CTF_ABI = [{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}]
NEG_RISK_ABI = [{"name":"redeemPositions","type":"function","inputs":[{"name":"conditionId","type":"bytes32"},{"name":"amounts","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}]

def build_web3() -> Web3:
    rpc = _cfg("POLY_RPC", "https://polygon-rpc.com")
    w3 = Web3(Web3.HTTPProvider(rpc))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3

def parse_condition_id(condition_id: str) -> bytes:
    cid = condition_id.strip()
    if cid.startswith(("0x", "0X")):
        return bytes.fromhex(cid[2:].zfill(64))
    return bytes.fromhex(cid.zfill(64))

# --- CORE RELAYER FUNCTION (Fixed 401 & Logs) ---
def submit_to_relayer(eoa_address, proxy_wallet, to, data_hex, nonce, signature):
    api_key = _cfg("POLY_BUILDER_KEY")
    api_secret = _cfg("POLY_BUILDER_SECRET")
    api_pass = _cfg("POLY_BUILDER_PASSPHRASE")
    
    # Zaman farkını tolere etmek için senkronizasyon
    timestamp = str(int(time.time()))
    method = "POST"
    path = "/submit"
    
    payload = {
        "data": data_hex,
        "from": Web3.to_checksum_address(eoa_address),
        "metadata": "",
        "nonce": str(nonce),
        "proxyWallet": Web3.to_checksum_address(proxy_wallet),
        "signature": signature,
        "to": Web3.to_checksum_address(to),
        "type": "EOA"
    }
    
    body_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    message = f"{timestamp}{method}{path}{body_str}"
    sig_l2 = hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    headers = {
        "POLY-API-KEY": api_key,
        "POLY-SIGNATURE": sig_l2,
        "POLY-TIMESTAMP": timestamp,
        "POLY-PASSPHRASE": api_pass,
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(RELAYER_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code in (200, 201):
            result = resp.json()
            tx_hash = result.get('transactionHash') or result.get('hash')
            if tx_hash:
                log.info(f"    ✅ BAŞARILI! Hash: {tx_hash}")
                log.info(f"    Takip: https://polygonscan.com/tx/{tx_hash}")
            return result
        else:
            log.error(f"    ❌ RELAYER REDDİ: {resp.status_code} - {resp.text}")
            return None
    except Exception as exc:
        log.error(f"    ❌ Bağlantı hatası: {exc}")
        return None

def encode_redeem_calldata(w3, condition_id):
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
    cid = parse_condition_id(condition_id)
    return ctf.encode_abi("redeemPositions", args=[Web3.to_checksum_address(USDC_ADDRESS), b"\x00" * 32, cid, [1, 2]])

def sign_calldata(private_key, data_hex):
    data_bytes = bytes.fromhex(data_hex.removeprefix("0x"))
    msg_hash = Web3.keccak(data_bytes)
    signable = encode_defunct(primitive=msg_hash)
    return Account.from_key(private_key).sign_message(signable).signature.hex()

def redeem_via_relayer(w3, condition_id, size, neg_risk, private_key, eoa_address, proxy_wallet):
    try:
        data_hex = encode_redeem_calldata(w3, condition_id)
        to = CTF_ADDRESS
        signature = sign_calldata(private_key, data_hex)
        if not signature.startswith("0x"): signature = "0x" + signature
        
        return submit_to_relayer(eoa_address, proxy_wallet, to, data_hex, 0, signature)
    except Exception as e:
        log.error(f"Redeem error: {e}")
        return False

def fetch_all_positions(wallet):
    try:
        resp = requests.get(f"{DATA_API}/positions", params={"user": wallet, "limit": "500"}, timeout=15)
        return [p for p in resp.json() if float(p.get("size", 0)) > ZERO_THRESHOLD]
    except: return []

def execute_redemptions(proxy_wallet, eoa_address, w3, private_key, already_claimed, redeemable):
    if not redeemable: return
    by_condition = {}
    for pos in redeemable:
        cid = pos.get("conditionId", "")
        if cid: by_condition.setdefault(cid, []).append(pos)

    pending = {cid: grp for cid, grp in by_condition.items() if cid not in already_claimed}
    if not pending: return

    for cid, group in pending.items():
        log.info(f"Redeeming condition: {cid}")
        success = redeem_via_relayer(w3, cid, 0, False, private_key, eoa_address, proxy_wallet)
        if success: already_claimed.add(cid)
        time.sleep(TX_DELAY_SECONDS)

def run():
    pk = _cfg("POLY_PRIVATE_KEY")
    pw = _cfg("FUNDER_ADDRESS")
    w3 = build_web3()
    account = Account.from_key(pk)
    already_claimed = set()
    
    log.info(f"Bot Active - Wallet: {pw} - Method: {CLAIM_METHOD}")
    
    while True:
        try:
            all_pos = fetch_all_positions(pw)
            redeemable = [p for p in all_pos if (float(p.get("curPrice", 0.5)) >= RESOLVED_HIGH or float(p.
