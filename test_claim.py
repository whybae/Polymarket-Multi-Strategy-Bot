import os
import logging
import requests
import time
from web3 import Web3

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] >>> %(message)s')
log = logging.getLogger("SDK-Final-Fix")

# KÜTÜPHANE İÇE AKTARMA (Farklı yollar deneniyor)
try:
    # En yaygın SDK yolu
    from py_builder_signing_sdk.config.builder_config import BuilderConfig
    from py_builder_signing_sdk.creds.api_key_creds import BuilderApiKeyCreds
    from py_builder_relayer_client.client import RelayClient
    LIBS_OK = True
except ImportError:
    try:
        # Alternatif SDK yolu
        from py_builder_signing_sdk import BuilderConfig, BuilderApiKeyCreds
        from py_builder_relayer_client import RelayClient
        LIBS_OK = True
    except ImportError as e:
        LIBS_OK = False
        log.error(f"❌ SDK Yükleme Hatası: {e}")

def run_sdk_test():
    if not LIBS_OK:
        log.error("Kütüphaneler yüklü ama içindeki 'BuilderConfig' sınıfı bulunamıyor. Lütfen SDK versiyonunu kontrol edin.")
        return

    # DEĞİŞKENLER
    k = os.getenv("POLY_BUILDER_KEY")
    s = os.getenv("POLY_BUILDER_SECRET")
    p = os.getenv("POLY_BUILDER_PASSPHRASE")
    pk = os.getenv("POLY_PRIVATE_KEY")
    pw = os.getenv("FUNDER_ADDRESS")

    try:
        # SDK BAŞLATMA
        creds = BuilderApiKeyCreds(key=k, secret=s, passphrase=p)
        config = BuilderConfig(local_builder_creds=creds)
        client = RelayClient("https://relayer-v2.polymarket.com", 137, pk, config)
        
        log.info("🚀 SDK Başarıyla Başlatıldı!")

        # POZİSYON TARAMA
        r = requests.get(f"https://data-api.polymarket.com/positions?user={pw}&limit=1")
        if not r.json():
            log.info("Cüzdanda aktif pozisyon bulunamadı.")
            return
            
        cid = r.json()[0]['conditionId']
        log.info(f"İşlem yapılacak Condition: {cid}")

        # REDEEM İŞLEMİ
        USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        
        w3 = Web3()
        abi = [{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}]
        contract = w3.eth.contract(address=CTF, abi=abi)
        
        calldata = contract.encode_abi("redeemPositions", [
            Web3.to_checksum_address(USDC), 
            b"\x00"*32, 
            bytes.fromhex(cid[2:].zfill(64)), 
            [1, 2]
        ])

        tx = {"to": CTF, "data": calldata, "value": "0"}

        # GASLESS EXECUTION
        log.info("Relayer üzerinden işlem gönderiliyor...")
        response = client.execute([tx], "SDK Redeem")
        
        log.info("Ağ onayı bekleniyor (minden/confirmed)...")
        result = response.wait()
        log.info(f"✅ BİNGO! İşlem Başarılı: {result.get('transactionHash')}")

    except Exception as e:
        log.error(f"Sistem Hatası: {e}")

if __name__ == "__main__":
    run_sdk_test()
