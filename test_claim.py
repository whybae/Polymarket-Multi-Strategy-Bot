import os
import logging
import requests
import time
from web3 import Web3

# LOGLAMA
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] >>> %(message)s')
log = logging.getLogger("DependencyCheck")

# KÜTÜPHANE KONTROLÜ
try:
    from py_builder_relayer_client.client import RelayClient
    from py_builder_signing_sdk import BuilderConfig, BuilderApiKeyCreds
    LIBS_OK = True
    log.info("✅ BAŞARILI: Polymarket SDK kütüphaneleri yüklü.")
except ImportError as e:
    LIBS_OK = False
    log.error(f"❌ HATA: Kütüphane bulunamadı! Detay: {e}")
    log.error("İpucu: Railway panelinde 'Rebuild' yaparak requirements.txt'yi tekrar okutun.")

def run_sdk_test():
    if not LIBS_OK:
        log.warning("Kütüphaneler eksik olduğu için SDK testi başlatılamıyor.")
        return

    # DEĞİŞKENLER
    k = os.getenv("POLY_BUILDER_KEY")
    s = os.getenv("POLY_BUILDER_SECRET")
    p = os.getenv("POLY_BUILDER_PASSPHRASE")
    pk = os.getenv("POLY_PRIVATE_KEY")
    pw = os.getenv("FUNDER_ADDRESS")

    try:
        # SDK CONFIG
        creds = BuilderApiKeyCreds(key=k, secret=s, passphrase=p)
        config = BuilderConfig(local_builder_creds=creds)
        client = RelayClient("https://relayer-v2.polymarket.com", 137, pk, config)
        
        log.info("--- SDK BAĞLANTISI KURULDU ---")
        
        # POZİSYON TARAMA
        r = requests.get(f"https://data-api.polymarket.com/positions?user={pw}&limit=1")
        if not r.json():
            log.info("Cüzdanda aktif pozisyon yok.")
            return
            
        cid = r.json()[0]['conditionId']
        log.info(f"İşlem yapılacak Condition: {cid}")

        # İŞLEM PAKETİ (REDEEM)
        USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        
        w3 = Web3()
        contract = w3.eth.contract(address=CTF, abi=[{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}])
        calldata = contract.encode_abi("redeemPositions", [Web3.to_checksum_address(USDC), b"\x00"*32, bytes.fromhex(cid[2:].zfill(64)), [1, 2]])

        tx = {"to": CTF, "data": calldata, "value": "0"}

        log.info("🚀 SDK üzerinden 'execute' çağrılıyor...")
        response = client.execute([tx], "SDK Claim Test")
        
        result = response.wait()
        log.info(f"✅ İŞLEM TAMAMLANDI! Hash: {result.get('transactionHash')}")

    except Exception as e:
        log.error(f"Sistem Hatası: {e}")

if __name__ == "__main__":
    run_sdk_test()
