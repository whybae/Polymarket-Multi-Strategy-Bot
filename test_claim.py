import os
import logging
import requests
from py_builder_relayer_client.client import RelayClient
from py_builder_signing_sdk import BuilderConfig, BuilderApiKeyCreds

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] >>> %(message)s')
log = logging.getLogger("SDK-Test")

def run_sdk_test():
    # 1. Credentials (Railway Variables)
    creds = BuilderApiKeyCreds(
        key=os.getenv("POLY_BUILDER_KEY"),
        secret=os.getenv("POLY_BUILDER_SECRET"),
        passphrase=os.getenv("POLY_BUILDER_PASSPHRASE")
    )
    
    config = BuilderConfig(local_builder_creds=creds)
    pk = os.getenv("POLY_PRIVATE_KEY")
    pw = os.getenv("FUNDER_ADDRESS")

    # 2. Client Setup (Dökümana %100 uygun)
    # 137 = Polygon Mainnet
    client = RelayClient("https://relayer-v2.polymarket.com", 137, pk, config)

    log.info("--- SDK TABANLI OPERASYON BAŞLADI ---")

    try:
        # 3. Pozisyon Bul
        r = requests.get(f"https://data-api.polymarket.com/positions?user={pw}&limit=1")
        cid = r.json()[0]['conditionId']
        log.info(f"Hedef Condition: {cid}")

        # 4. İşlemi Oluştur (Dökümandaki gibi)
        # Buradaki 'execute' metodu dökümanda gösterildiği gibi her şeyi otomatik imzalar
        # Hem EOA imzasını hem Builder L2 imzasını kütüphane halleder.
        
        # CTF Redeem Verisi
        from web3 import Web3
        USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        
        data = Web3().eth.contract(address=CTF, abi=[{
            "name": "redeemPositions", "type": "function",
            "inputs": [
                {"name": "collateralToken", "type": "address"},
                {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "indexSets", "type": "uint256[]"}
            ], "outputs": []
        }]).encode_abi("redeemPositions", [USDC, b"\x00"*32, bytes.fromhex(cid[2:].zfill(64)), [1, 2]])

        tx = {"to": CTF, "data": data, "value": "0"}

        log.info("🚀 SDK üzerinden işlem gönderiliyor (Gasless)...")
        response = client.execute([tx], "Redeem Positions via SDK")
        
        # Wait for transaction
        log.info("İşlem gönderildi, onay bekleniyor...")
        result = response.wait()
        log.info(f"✅ BAŞARILI! İşlem Hash: {result.get('transactionHash')}")

    except Exception as e:
        log.error(f"❌ SDK HATASI: {e}")

if __name__ == "__main__":
    run_sdk_test()
