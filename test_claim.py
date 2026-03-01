import hmac, hashlib, time, requests, json, os, logging
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger("LabTest")

# Sabitler (Polymarket V2)
RELAYER_URL = "https://relayer-v2.polymarket.com/submit"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

def run_submit_test():
    # 1. Değişkenleri Railway'den al
    key = os.environ.get("POLY_BUILDER_KEY", "").strip()
    secret = os.environ.get("POLY_BUILDER_SECRET", "").strip()
    passphrase = os.environ.get("POLY_BUILDER_PASSPHRASE", "").strip()
    eoa_pk = os.environ.get("POLY_PRIVATE_KEY", "").strip()
    proxy_wallet = os.environ.get("FUNDER_ADDRESS", "").strip()

    # 2. Örnek bir Condition ID (Cüzdanındaki o bekleyenlerden birini alalım)
    # Eğer cüzdanında bekleyen varsa kod bunu otomatik bulmaya çalışacak
    log.info("--- GERÇEK SUBMİT PROVASI BAŞLADI ---")
    
    try:
        # Cüzdanındaki pozisyonları tara
        r_pos = requests.get(f"https://data-api.polymarket.com/positions?user={proxy_wallet}&limit=5", timeout=10)
        positions = [p for p in r_pos.json() if p.get("redeemable")]
        
        if not positions:
            log.error("❌ HATA: Cüzdanında şu an claim edilecek pozisyon yok!")
            return

        target_pos = positions[0]
        cid = target_pos.get("conditionId")
        log.info(f"Hedef Condition: {cid}")

        # 3. Data Hex Oluştur (RedeemPositions Calldata)
        # Basit bir ABI ile kodlama
        w3 = Web3()
        ctf_contract = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=[{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}])
        
        cid_bytes = bytes.fromhex(cid[2:].zfill(64))
        data_hex = ctf_contract.encode_abi("redeemPositions", args=[Web3.to_checksum_address(USDC_ADDRESS), b"\x00" * 32, cid_bytes, [1, 2]])

        # 4. EOA İmzası (Cüzdanın işlemi onaylaması)
        msg_hash = Web3.keccak(bytes.fromhex(data_hex.removeprefix("0x")))
        account = Account.from_key(eoa_pk)
        eoa_sig = account.sign_message(encode_defunct(primitive=msg_hash)).signature.hex()
        if not eoa_sig.startswith("0x"): eoa_sig = "0x" + eoa_sig

        # 5. Builder Payload ve L2 İmzası
        timestamp = str(int(time.time()))
        payload = {
            "data": data_hex,
            "from": Web3.to_checksum_address(account.address),
            "metadata": "",
            "nonce": "0",
            "proxyWallet": Web3.to_checksum_address(proxy_wallet),
            "signature": eoa_sig,
            "to": Web3.to_checksum_address(CTF_ADDRESS),
            "type": "EOA"
        }
        
        # Kesinlikle boşluksuz ham metin
        body_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        message = f"{timestamp}POST/submit{body_str}"
        sig_l2 = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

        headers = {
            "POLY-API-KEY": key,
            "POLY-SIGNATURE": sig_l2,
            "POLY-TIMESTAMP": timestamp,
            "POLY-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        log.info("🚀 Relayer'a gerçek veri gönderiliyor...")
        resp = requests.post(RELAYER_URL, data=body_str, headers=headers, timeout=15)
        
        log.info(f"YANIT KODU: {resp.status_code}")
        log.info(f"YANIT: {resp.text}")

        if resp.status_code in (200, 201):
            log.info("🔥 BAŞARDIK! İşlem Relayer tarafından kabul edildi.")
        else:
            log.error("⚠️ Hala bir sorun var. Yukarıdaki hata mesajını incele.")

    except Exception as e:
        log.error(f"HATA: {e}")

if __name__ == "__main__":
    run_submit_test()
