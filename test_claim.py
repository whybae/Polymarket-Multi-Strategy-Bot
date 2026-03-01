import os
import logging
import requests
import time
from web3 import Web3
from eth_account import Account

# Kütüphanelerin yüklenip yüklenmediğini kontrol ederek import et
try:
    from py_builder_relayer_client import RelayClient
    from py_builder_signing_sdk import BuilderConfig, BuilderApiKeyCreds
except ImportError:
    print("HATA: Kütüphaneler bulunamadı. Lütfen requirements.txt dosyasına ekleyin.")

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] >>> %(message)s')
log = logging.getLogger("SDK-Fix")

def run_sdk_test():
    # 1. Credentials
    k = os.getenv("POLY_BUILDER_KEY")
    s = os.getenv("POLY_BUILDER_SECRET")
    p = os.getenv("POLY_BUILDER_PASSPHRASE")
    pk = os.getenv("POLY_PRIVATE_KEY")
    pw = os.getenv("FUNDER_ADDRESS")

    if not all([k, s, p, pk, pw]):
        log.error("Eksik değişkenler var! Lütfen Railway Variables sekmesini kontrol edin.")
        return

    # 2. SDK Yapılandırması
    creds = BuilderApiKeyCreds(key=k, secret=s, passphrase=p)
    config = BuilderConfig(local_builder_creds=creds)

    # 3. Client Başlatma (Polygon Mainnet: 137)
    try:
        client = RelayClient("https://relayer-v2.polymarket.com", 137, pk, config)
        log.info("--- SDK TABANLI OPERASYON BAŞLADI ---")

        # 4. Pozisyon Bulma
        r = requests.get(f"https://data-api.polymarket.com/positions?user={pw}&limit=1")
        data_json = r.json()
        if not data_json:
            log.warning("Cüzdanda pozisyon bulunamadı.")
            return
            
        cid = data_json[0]['conditionId']
        log.info(f"Hedef Condition: {cid}")

        # 5. Redeem Verisi Hazırlama
        USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        
        w3 = Web3()
        contract = w3.eth.contract(address=CTF, abi=[{
            "name": "redeemPositions", "type": "function",
            "inputs": [
                {"name": "collateralToken", "type": "address"},
                {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "indexSets", "type": "uint256[]"}
            ], "outputs": []
        }])
        
        calldata = contract.encode_abi("redeemPositions", [
            Web3.to_checksum_address(USDC), 
            b"\x00"*32, 
            bytes.fromhex(cid[2:].zfill(64)), 
            [1, 2]
        ])

        tx = {"to": CTF, "data": calldata, "value": "0"}

        log.info("🚀 SDK üzerinden işlem gönderiliyor...")
        # SDK execute metodu dökümandaki gibi tüm imzalama işlerini yapar
        response = client.execute([tx], "Redeem Positions via SDK")
        
        log.info("Bekleniyor...")
        result = response.wait()
        log.info(f"✅ BAŞARILI! Hash: {result.get('transactionHash')}")

    except Exception as e:
        log.error(f"❌ SDK HATASI: {e}")

if __name__ == "__main__":
    run_sdk_test()
