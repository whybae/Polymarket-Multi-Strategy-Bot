import time, requests, json, os, hmac, hashlib, logging

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] >>> %(message)s')
log = logging.getLogger("SimpleTest")

def run_final_test():
    # Railway'den temiz verileri al
    key = os.environ.get("POLY_BUILDER_KEY", "").strip()
    secret = os.environ.get("POLY_BUILDER_SECRET", "").strip()
    passphrase = os.environ.get("POLY_BUILDER_PASSPHRASE", "").strip()

    log.info("=== BASİTLEŞTİRİLMİŞ BUILDER TESTİ ===")
    
    timestamp = str(int(time.time()))
    # Bu sefer '/submit' yerine direkt ana URL'yi veya profil uç noktasını hedefleyelim
    # Bazı Builder API'leri sadece GET isteğiyle bile doğrulanabilir
    
    method = "GET"
    path = "/orders" # Bu uç nokta Builder yetkisi gerektirir ama body istemez
    
    # İmzalanacak mesaj (Body boş olduğu için sadece timestamp+method+path)
    message = f"{timestamp}{method}{path}"
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    headers = {
        "POLY-API-KEY": key,
        "POLY-SIGNATURE": sig,
        "POLY-TIMESTAMP": timestamp,
        "POLY-PASSPHRASE": passphrase,
        "Content-Type": "application/json"
    }

    try:
        url = "https://clob.polymarket.com/orders" # Farklı bir endpoint deniyoruz
        log.info(f"Bağlanılıyor: {url}")
        r = requests.get(url, headers=headers, timeout=10)
        log.info(f"YANIT KODU: {r.status_code}")
        log.info(f"MESAJ: {r.text}")
    except Exception as e:
        log.error(f"HATA: {e}")

if __name__ == "__main__":
    run_final_test()
    time.sleep(60)
