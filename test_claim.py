import hmac, hashlib, time, requests, json, os, logging
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

# Loglama Ayarları
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] >>> %(message)s')
log = logging.getLogger("PolymarketFinal")

def run_documented_test():
    # 1. Değişkenleri Railway'den Çek
    k = os.environ.get("POLY_BUILDER_KEY", "").strip()
    s = os.environ.get("POLY_BUILDER_SECRET", "").strip()
    p = os.environ.get("POLY_BUILDER_PASSPHRASE", "").strip()
    pk = os.environ.get("POLY_PRIVATE_KEY", "").strip()
    pw = os.environ.get("FUNDER_ADDRESS", "").strip()

    log.info("--- DÖKÜMAN UYUMLU OPERASYON BAŞLADI ---")

    try:
        # 2. Cüzdan Pozisyonlarını Tara
        r_pos = requests.get(f"https://data-api.polymarket.com/positions?user={pw}&limit=1", timeout=10)
        pos_data = r_pos.json()
        if not pos_data:
            log.error("Cüzdanda çekilecek pozisyon bulunamadı!")
            return
            
        target = pos_data[0]
        cid = target.get("conditionId")
        log.info(f"Hedef: {target.get('title', 'Market')}")

        # 3. Calldata Hazırlığı
        w3 = Web3()
        CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        
        abi = [{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}]
        contract = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=abi)
        
        cid_bytes = bytes.fromhex(cid[2:].zfill(64))
        data_hex = contract.encode_abi("redeemPositions", [Web3.to_checksum_address(USDC), b"\x00" * 32, cid_bytes, [1, 2]])

        # 4. EOA L1 İmzası
        account = Account.from_key(pk)
        msg_hash = Web3.keccak(bytes.fromhex(data_hex.removeprefix("0x")))
        eoa_sig = account.sign_message(encode_defunct(primitive=msg_hash)).signature.hex()
        if not eoa_sig.startswith("0x"): eoa_sig = "0x" + eoa_sig

        # 5. Body (Payload) - Strict JSON
        payload = {
            "data": data_hex,
            "from": Web3.to_checksum_address(account.address),
            "metadata": "",
            "nonce": int(time.time() * 1000),
            "proxyWallet": Web3.to_checksum_address(pw),
            "signature": eoa_sig,
            "to": Web3.to_checksum_address(CTF),
            "type": "GNOSIS_SAFE"
        }
        
        body_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)

        # 6. HMAC L2 İmza (Hata burada düzeltildi)
        timestamp = str(int(time.time()))
        method = "POST"
        path = "/submit"
        
        sig_message = f"{timestamp}{method}{path}{body_str}"
        # .hexdigest() eklendi, sondaki hatalı nokta silindi
        signature = hmac.new(s.encode(), sig_message.encode(), hashlib.sha256).hexdigest()

        # 7. Headerlar
        headers = {
            "POLY-API-KEY": k,
            "POLY-SIGNATURE": signature,
            "POLY-TIMESTAMP": timestamp,
            "POLY-PASSPHRASE": p,
            "Content-Type": "application/json"
        }

        # 8. Gönderim
        log.info("🚀 Relayer'a istek gönderiliyor...")
        resp = requests.post("https://relayer-v2.polymarket.com/submit", data=body_str, headers=headers, timeout=20)

        log.info(f"DURUM: {resp.status_code}")
        log.info(f"YANIT: {resp.text}")

    except Exception as e:
        log.error(f"Sistem Hatası: {e}")

if __name__ == "__main__":
    run_documented_test()
