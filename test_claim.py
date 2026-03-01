import hmac, hashlib, time, requests, json, os, logging
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger("LabTest")

def run_final_diagnostic():
    key = os.environ.get("POLY_BUILDER_KEY", "").strip()
    secret = os.environ.get("POLY_BUILDER_SECRET", "").strip()
    api_pass = os.environ.get("POLY_BUILDER_PASSPHRASE", "").strip()
    pk = os.environ.get("POLY_PRIVATE_KEY", "").strip()
    pw = os.environ.get("FUNDER_ADDRESS", "").strip()

    log.info("--- 401 DUVARINI YIKMA OPERASYONU ---")

    try:
        # Pozisyonu bul
        r_pos = requests.get(f"https://data-api.polymarket.com/positions?user={pw}&limit=5")
        target = [p for p in r_pos.json() if float(p.get("size", 0)) > 0][0]
        cid = target.get("conditionId")
        
        # Data Hex ve EOA Sig (Bunlar standart, değişmez)
        w3 = Web3()
        cid_bytes = bytes.fromhex(cid[2:].zfill(64))
        data_hex = w3.eth.contract(address=Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"), abi=[{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}]).encode_abi("redeemPositions", args=[Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"), b"\x00" * 32, cid_bytes, [1, 2]])
        eoa_sig = Account.from_key(pk).sign_message(encode_defunct(primitive=Web3.keccak(bytes.fromhex(data_hex.removeprefix("0x"))))).signature.hex()

        payload = {
            "data": data_hex,
            "from": Web3.to_checksum_address(Account.from_key(pk).address),
            "metadata": "",
            "nonce": "0",
            "proxyWallet": Web3.to_checksum_address(pw),
            "signature": eoa_sig if eoa_sig.startswith("0x") else "0x"+eoa_sig,
            "to": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
            "type": "EOA"
        }
        
        body_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        timestamp = str(int(time.time()))

        # DENENECEK 3 FARKLI İMZA DİZİLİMİ
        variations = [
            f"{timestamp}POST/submit{body_str}",  # Varyasyon 1 (Standart V2)
            f"{timestamp}{body_str}",             # Varyasyon 2 (Yalın)
            f"{timestamp}POST{body_str}"          # Varyasyon 3 (Path'siz)
        ]

        for i, msg in enumerate(variations, 1):
            sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
            headers = {
                "POLY-API-KEY": key,
                "POLY-SIGNATURE": sig,
                "POLY-TIMESTAMP": timestamp,
                "POLY-PASSPHRASE": api_pass,
                "Content-Type": "application/json"
            }
            
            log.info(f"Deneniyor Varyasyon {i}...")
            r = requests.post("https://relayer-v2.polymarket.com/submit", data=body_str, headers=headers)
            
            if r.status_code in (200, 201):
                log.info(f"🔥 BİNGO! Varyasyon {i} çalıştı!")
                log.info(f"Yanıt: {r.text}")
                return
            else:
                log.info(f"Varyasyon {i} başarısız (HTTP {r.status_code})")

    except Exception as e:
        log.error(f"Hata: {e}")

if __name__ == "__main__":
    run_final_diagnostic()
