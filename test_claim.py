import hmac, hashlib, time, requests, json, os, logging, random
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] >>> %(message)s')
log = logging.getLogger("RelayerFix")

def run_relayer_fix():
    # 1. Variables (Railway'den hatasız çekim)
    key = os.environ.get("POLY_BUILDER_KEY", "").strip()
    secret = os.environ.get("POLY_BUILDER_SECRET", "").strip()
    passphrase = os.environ.get("POLY_BUILDER_PASSPHRASE", "").strip()
    pk = os.environ.get("POLY_PRIVATE_KEY", "").strip()
    pw = os.environ.get("FUNDER_ADDRESS", "").strip()

    log.info("--- RELAYER 401 ÇÖZÜM OPERASYONU ---")

    try:
        # 2. Cüzdandaki pozisyonu yakala
        r_pos = requests.get(f"https://data-api.polymarket.com/positions?user={pw}&limit=5")
        target = [p for p in r_pos.json() if float(p.get("size", 0)) > 0][0]
        cid = target.get("conditionId")
        
        # 3. Data Hex & EOA Signature (Standart)
        w3 = Web3()
        cid_bytes = bytes.fromhex(cid[2:].zfill(64))
        data_hex = w3.eth.contract(address=Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"), abi=[{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}]).encode_abi("redeemPositions", args=[Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"), b"\x00" * 32, cid_bytes, [1, 2]])
        eoa_sig = Account.from_key(pk).sign_message(encode_defunct(primitive=Web3.keccak(bytes.fromhex(data_hex.removeprefix("0x"))))).signature.hex()
        if not eoa_sig.startswith("0x"): eoa_sig = "0x" + eoa_sig

        # 4. Payload (Nonce artık rastgele/artan bir değer olacak)
        # Bazı V2 versiyonları milisaniye bazlı nonce bekler.
        test_nonce = int(time.time() * 1000) 
        
        payload = {
            "data": data_hex,
            "from": Web3.to_checksum_address(Account.from_key(pk).address),
            "metadata": "",
            "nonce": str(test_nonce), # Kritik Değişiklik: "0" yerine dinamik nonce
            "proxyWallet": Web3.to_checksum_address(pw),
            "signature": eoa_sig,
            "to": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
            "type": "EOA"
        }
        
        body_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        
        # 5. Farklı İmza Mesajı Formatlarını Dene (Varyasyonel Teşhis)
        timestamp = str(int(time.time()))
        
        # V2 Relayer'ın en çok kabul ettiği 2 ana format
        variations = [
            f"{timestamp}POST/submit{body_str}", # Standart
            f"{timestamp}{body_str}"              # Sade
        ]

        for i, msg in enumerate(variations, 1):
            sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
            headers = {
                "POLY-API-KEY": key,
                "POLY-SIGNATURE": sig,
                "POLY-TIMESTAMP": timestamp,
                "POLY-PASSPHRASE": passphrase,
                "Content-Type": "application/json"
            }
            
            log.info(f"Varyasyon {i} Deneniyor... (Nonce: {test_nonce})")
            r = requests.post("https://relayer-v2.polymarket.com/submit", data=body_str, headers=headers)
            
            if r.status_code in (200, 201):
                log.info(f"🔥 BAŞARILI! Varyasyon {i} ile 401 duvarı yıkıldı.")
                log.info(f"İşlem Hash: {r.json().get('transactionHash')}")
                return
            else:
                log.error(f"Varyasyon {i} REDDEDİLDİ: {r.status_code} - {r.text}")

    except Exception as e:
        log.error(f"Sistem Hatası: {e}")

if __name__ == "__main__":
    run_relayer_fix()
