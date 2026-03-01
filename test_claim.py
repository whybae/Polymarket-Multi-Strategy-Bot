import hmac, hashlib, time, requests, json, os, logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger("Diagnostic")

def run_diagnostic():
    # 1. Değişkenleri Railway'den oku
    keys = {
        "K": os.environ.get("POLY_BUILDER_KEY", "").strip(),
        "S": os.environ.get("POLY_BUILDER_SECRET", "").strip(),
        "P": os.environ.get("POLY_BUILDER_PASSPHRASE", "").strip()
    }

    log.info("--- DIAGNOSTIC START ---")
    log.info(f"API Key Length: {len(keys['K'])}")
    log.info(f"Secret Length: {len(keys['S'])}")
    log.info(f"Passphrase Length: {len(keys['P'])}")

    # 2. Polymarket'in "Auth" imzasını test etmek için en yalın GET isteği
    # Bu istek body gerektirmediği için hata payı %0'dır.
    timestamp = str(int(time.time()))
    method = "GET"
    path = "/orders" # Builder yetkisi gerektiren ama veri istemeyen uç nokta
    
    # İmza mesajı: timestamp + method + path (body yok)
    message = f"{timestamp}{method}{path}"
    sig = hmac.new(keys['S'].encode(), message.encode(), hashlib.sha256).hexdigest()

    headers = {
        "POLY-API-KEY": keys['K'],
        "POLY-SIGNATURE": sig,
        "POLY-TIMESTAMP": timestamp,
        "POLY-PASSPHRASE": keys['P'],
        "Content-Type": "application/json"
    }

    log.info(f"Testing Auth with timestamp: {timestamp}")
    try:
        # Relayer yerine ana CLOB adresini deneyelim, yetkiyi buradan teyit edelim
        r = requests.get("https://clob.polymarket.com/orders", headers=headers, timeout=10)
        log.info(f"RESPONSE CODE: {r.status_code}")
        log.info(f"RESPONSE TEXT: {r.text}")

        if r.status_code == 405:
            log.info("✅ TEŞHİS: ANAHTARLARIN DOĞRU! (405 bir başarı sinyalidir)")
        elif r.status_code == 401:
            log.info("❌ TEŞHİS: ANAHTARLAR VEYA PASSPHRASE HALA HATALI!")
    except Exception as e:
        log.error(f"Error: {e}")

if __name__ == "__main__":
    run_diagnostic()
