import hmac, hashlib, time, requests, json, os, logging
from web3 import Web3

# Loglama Ayarları - Daha görünür yapalım
logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s][%(levelname)s] >>> %(message)s'
)
log = logging.getLogger("TerminalTest")

def _cfg(key: str) -> str:
    # Railway Variables'tan çekmeye zorla
    val = os.environ.get(key, "").strip()
    return val

def run_diagnostic():
    log.info("=========================================")
    log.info("   POLYMARKET BUILDER V2 TEST ÜNİTESİ    ")
    log.info("=========================================")
    
    # Değişkenleri kontrol et
    keys = {
        "KEY": _cfg("POLY_BUILDER_KEY"),
        "SECRET": _cfg("POLY_BUILDER_SECRET"),
        "PASS": _cfg("POLY_BUILDER_PASSPHRASE"),
        "ADDR": _cfg("POLY_ADDRESS"),
        "PROXY": _cfg("FUNDER_ADDRESS")
    }

    # Hangisi eksikse tek tek söyle
    missing = [k for k, v in keys.items() if not v]
    if missing:
        log.error(f"❌ EKSİK DEĞİŞKENLER: {', '.join(missing)}")
        log.error("Lütfen Railway > Variables kısmını kontrol et.")
        return

    log.info(f"✅ Tüm anahtarlar yüklendi. Adres: {keys['ADDR'][:10]}...")
    
    # Test İsteği Hazırla
    timestamp = str(int(time.time()))
    payload = {
        "data": "0x", "from": keys['ADDR'], "metadata": "", "nonce": "0",
        "proxyWallet": keys['PROXY'], "signature": "0x", 
        "to": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045", "type": "EOA"
    }
    
    body = json.dumps(payload, separators=(',', ':'))
    message = f"{timestamp}POST/submit{body}"
    sig = hmac.new(keys['SECRET'].encode(), message.encode(), hashlib.sha256).hexdigest()

    headers = {
        "POLY-BUILDER-API-KEY": keys['KEY'],
        "POLY-BUILDER-SIGNATURE": sig,
        "POLY-BUILDER-TIMESTAMP": timestamp,
        "POLY-BUILDER-PASSPHRASE": keys['PASS'],
        "Content-Type": "application/json"
    }

    log.info("🚀 Polymarket sunucusuna bağlanılıyor...")
    try:
        r = requests.post("https://relayer-v2.polymarket.com/submit", json=payload, headers=headers, timeout=10)
        log.info(f"📡 SUNUCU YANITI: {r.status_code}")
        log.info(f"📄 MESAJ: {r.text}")
        
        if r.status_code == 400:
            log.info("🎯 TEBRİKLER! Sunucu seni tanıdı (Yetki Tamam). Sadece gönderdiğin veri (0x) boş olduğu için 400 verdi.")
    except Exception as e:
        log.error(f"💥 BAĞLANTI HATASI: {e}")

if __name__ == "__main__":
    run_diagnostic()
    log.info("=========================================")
    log.info("Test bitti. Logları görmen için 2 dakika bekliyorum...")
    time.sleep(120) # 2 dakika boyunca konteynerı açık tutar
