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

# --- Sabitler ---
CHECK_INTERVAL = int(_cfg("CLAIM_CHECK_INTERVAL", "60"))
CLAIM_METHOD = _cfg("CLAIM_METHOD", "relayer").lower()
DATA_API = "https://data-api.polymarket.com"
RELAYER_URL = "https://relayer-v2.polymarket.com/submit"
CHAIN_ID = 137
RESOLVED_HIGH = 0.99
RESOLVED_LOW = 0.01
ZERO_THRESHOLD = 0.0001
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

CTF_ABI = [{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}]

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

def submit_to_relayer(eoa_address, proxy_wallet, to, data_hex, nonce, signature):
    api_key = _cfg("POLY_BUILDER_KEY")
    api_secret = _cfg("POLY_BUILDER_SECRET")
    api_pass = _cfg("POLY_BUILDER_PASSPHRASE")
    
    # 401 Hatasını Çözen Zaman Ayarı: 
    # Sunucu saat farkını tolere etmek için 1 saniye ileri alıyoruz
    timestamp = str(int(time.time() + 1))
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
    
    # Body: Kesinlikle boşluksuz ve sıralı
    body_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    
    # MESAJ DİZİLİMİ: Bazı Relayer'lar PATH sonuna '/' ister veya istemez.
    # En standart çalışan format: timestamp + method + path + body
    message = f"{timestamp}{method}{path}{body_str}"
    
    sig_l2 = hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    # HEADER İSİMLERİ: Kesinlikle Tire (-) kullanılmalı
    headers = {
        "POLY-API-KEY": api_key,
        "POLY-SIGNATURE": sig_l2,
        "POLY-TIMESTAMP": timestamp,
        "POLY-PASSPHRASE": api_pass,
        "Content-Type": "application/json"
    }

    log.info(f"    🚀 Relayer-V2 Deneniyor... (TS: {timestamp})")

    try:
        resp = requests.post(RELAYER_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code in (200, 201):
            result = resp.json()
            tx_hash = result.get('transactionHash') or result.get('hash')
            if tx_hash:
                log.info(f"    ✅ BAŞARILI! Hash: {tx_hash}")
                log.info(f"    Link: https://polygonscan.com/tx/{tx_hash}")
            return result
        else:
            log.error(f"    ❌ RED: {resp.status_code} - {resp.text}")
            # Eğer hala 401 geliyorsa, zamanı 1 saniye geri çekip tekrar deneme mekanizması eklenebilir
            return None
    except Exception as e:
        log.error(f"    ❌ Hata: {e}")
        return None
def run():
    pk = _cfg("POLY_PRIVATE_KEY")
    pw = _cfg("FUNDER_ADDRESS")
    w3 = build_web3()
    account = Account.from_key(pk)
    already_claimed = set()
    
    log.info(f"Bot Başlatıldı - Cüzdan: {pw}")
    
    while True:
        try:
            resp = requests.get(f"{DATA_API}/positions", params={"user": pw, "limit": "500"}, timeout=15)
            all_pos = [p for p in resp.json() if float(p.get("size", 0)) > ZERO_THRESHOLD]
            
            redeemable = [p for p in all_pos if (float(p.get("curPrice", 0.5)) >= RESOLVED_HIGH or float(p.get("curPrice", 0.5)) <= RESOLVED_LOW) and p.get("redeemable")]
            
            if redeemable:
                for pos in redeemable:
                    cid = pos.get("conditionId")
                    if cid and cid not in already_claimed:
                        log.info(f"Claim ediliyor: {cid}")
                        ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
                        data_hex = ctf.encode_abi("redeemPositions", args=[Web3.to_checksum_address(USDC_ADDRESS), b"\x00" * 32, parse_condition_id(cid), [1, 2]])
                        
                        msg_hash = Web3.keccak(bytes.fromhex(data_hex.removeprefix("0x")))
                        signature = Account.from_key(pk).sign_message(encode_defunct(primitive=msg_hash)).signature.hex()
                        if not signature.startswith("0x"): signature = "0x" + signature
                        
                        success = submit_to_relayer(account.address, pw, CTF_ADDRESS, data_hex, 0, signature)
                        if success: already_claimed.add(cid)
                        time.sleep(2)
            
        except Exception as e:
            log.error(f"Döngü hatası: {e}")
        
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run()

