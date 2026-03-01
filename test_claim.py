import os
import logging
import requests
import time
from web3 import Web3

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] >>> %(message)s')
log = logging.getLogger("SDK-Explorer")

# --- AKILLI KEŞİF BLOĞU ---
def get_sdk_components():
    # 1. Adım: Ana modülleri içeri aktar
    try:
        import py_builder_relayer_client as pbrc
        import py_builder_signing_sdk as pbss
        log.info("Kütüphaneler sistemde bulundu, sınıflar aranıyor...")
    except ImportError:
        return None, None, None

    # 2. Adım: BuilderConfig ve BuilderApiKeyCreds sınıflarını kütüphane içinde tara
    # Kütüphane versiyon farklarına göre sınıfları bulur
    def find_class(module, class_name):
        # Doğrudan ana modülde mi?
        if hasattr(module, class_name):
            return getattr(module, class_name)
        # Alt modüllerde mi? (Örn: pbss.BuilderConfig veya pbss.config.BuilderConfig)
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if hasattr(attr, class_name):
                return getattr(attr, class_name)
        return None

    b_config = find_class(pbss, "BuilderConfig")
    b_creds = find_class(pbss, "BuilderApiKeyCreds")
    r_client = find_class(pbrc, "RelayClient")

    return b_config, b_creds, r_client

BuilderConfig, BuilderApiKeyCreds, RelayClient = get_sdk_components()

def run_final_push():
    if not all([BuilderConfig, BuilderApiKeyCreds, RelayClient]):
        log.error("HATA: Kütüphane yüklü ama sınıflar (BuilderConfig vb.) bulunamadı.")
        # Debug: Kütüphane içeriğini logla (neyin yanlış olduğunu görmek için)
        import py_builder_signing_sdk
        log.info(f"Kütüphane İçeriği: {dir(py_builder_signing_sdk)}")
        return

    # DEĞİŞKENLER
    k, s, p = os.getenv("POLY_BUILDER_KEY"), os.getenv("POLY_BUILDER_SECRET"), os.getenv("POLY_BUILDER_PASSPHRASE")
    pk, pw = os.getenv("POLY_PRIVATE_KEY"), os.getenv("FUNDER_ADDRESS")

    try:
        # SDK BAŞLATMA
        creds = BuilderApiKeyCreds(key=k, secret=s, passphrase=p)
        config = BuilderConfig(local_builder_creds=creds)
        client = RelayClient("https://relayer-v2.polymarket.com", 137, pk, config)
        
        log.info("🚀 SDK Başarıyla Bağlandı!")

        # POZİSYON BULMA
        r = requests.get(f"https://data-api.polymarket.com/positions?user={pw}&limit=1")
        if not r.json():
            log.info("Çekilecek pozisyon kalmadı.")
            return
        cid = r.json()[0]['conditionId']

        # REDEEM DATA
        w3 = Web3()
        contract_addr = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        abi = [{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}]
        data = w3.eth.contract(address=contract_addr, abi=abi).encode_abi("redeemPositions", [
            "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", b"\x00"*32, bytes.fromhex(cid[2:].zfill(64)), [1, 2]
        ])

        # EXECUTE
        log.info(f"Redeeming: {cid}")
        response = client.execute([{"to": contract_addr, "data": data, "value": "0"}], "SDK Claim")
        
        log.info("İşlem Relayer'da, onay bekleniyor...")
        result = response.wait()
        log.info(f"✅ İŞLEM BAŞARILI: {result.get('transactionHash')}")

    except Exception as e:
        log.error(f"Hata: {e}")

if __name__ == "__main__":
    run_final_push()
