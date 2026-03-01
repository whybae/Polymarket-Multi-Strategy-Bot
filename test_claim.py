import hmac, hashlib, time, requests, json, os, logging
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

# Loglama ayarları
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] >>> %(message)s')
log = logging.getLogger("LabTest")

# Sabitler
RELAYER_URL = "https://relayer-v2.polymarket.com/submit"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

def run_agressive_test():
    # 1. Değişkenleri Railway'den temizle ve al
    key = os.environ.get("POLY_BUILDER_KEY", "").strip()
    secret = os.environ.get("POLY_BUILDER_SECRET", "").strip()
    passphrase = os.environ.get("POLY_BUILDER_PASSPHRASE", "").strip()
    private_key = os.environ.get("POLY_PRIVATE_KEY", "").strip()
    proxy_wallet = os.environ.get("FUNDER_ADDRESS", "").strip()

    log.info("--- AGRESİF TEST: POZİSYON TARAMA VE SUBMİT ---")

    try:
        # 2. Cüzdandaki TÜM pozisyonları bul (filtresiz)
        r_pos = requests.get(f"https://data-api.polymarket.com/positions?user={proxy_wallet}&limit=30", timeout=10)
        all_positions = r_pos.json()
        
        # Sadece miktarı 0'dan büyük olanları alalım
        positions = [p for p in all_positions if float(p.get("size", 0)) > 0.0001]

        if not positions:
            log.error(f"❌ Cüzdanda ({proxy_wallet}) hiç pozisyon bulunamadı! Lütfen adresi kontrol et.")
            return

        log.info(f"Toplam {len(positions)} adet pozisyon bulundu. İlk uygun olan deneniyor...")

        target = positions[0]
        cid = target.get("conditionId")
        title = target.get("title", "Bilinmeyen Market")
        log.info(f"Hedef: {title}")
        log.info(f"Condition ID: {cid}")

        # 3. Data Hex (Calldata) hazırlama
        w3 = Web3()
        abi = [{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}]
        contract = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=abi)
        
        cid_bytes = bytes.fromhex(cid[2:].zfill(64))
        data_hex = contract.encode_abi("redeemPositions", args=[Web3.to_checksum_address(USDC_ADDRESS), b"\x00" * 32, cid_bytes, [1, 2]])

        # 4. EOA (Cüzdan Sahibi) İmzası
        account = Account.from_key(private_key)
        msg_hash = Web3.keccak(bytes.fromhex(data_hex.removeprefix("0x")))
        eoa_sig = account.sign_message(encode_defunct(primitive=msg_hash)).signature.hex()
        if not eoa_sig.startswith("0x"): eoa_sig = "0x" + eoa_sig

        # 5. Payload ve Gönderim (Ham Veri Metodu)
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
        
        body_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        timestamp = str(int(time.time()))
        
        # Builder V2 Signature
        message = f"{timestamp}POST/submit{body_str}"
        sig_l2 = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

        headers = {
            "POLY-API-KEY": key,
            "POLY-SIGNATURE": sig_l2,
            "POLY-TIMESTAMP": timestamp,
            "POLY-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        log.info("🚀 Relayer'a gönderiliyor...")
        resp = requests.post(RELAYER_URL, data=body_str, headers=headers, timeout=20)

        log.info(f"YANIT KODU: {resp.status_code}")
        log.info(f"SUNUCU MESAJI: {resp.text}")

    except Exception as e:
        log.error(f"Hata oluştu: {e}")

if __name__ == "__main__":
    run_agressive_test()
