import hmac, hashlib, time, requests, json, os, logging
from web3 import Web3

# Loglama Ayarları
logging.basicConfig(level=logging.INFO, format='[%(asctime)s][%(levelname)s][AutoClaim-Diag] - %(message)s')
log = logging.getLogger("Diag")

def _cfg(key: str) -> str:
    return os.environ.get(key, "").strip()

# TEST AYARLARI
# Bazı durumlarda v2 endpointi 'relayer-v2.polymarket.com' yerine 'clob.polymarket.com' altındadır
RELAYER_URL = "https://relayer-v2.polymarket.com/submit"

def test_auth_v2():
    log.info("=== DERİN TEŞHİS BAŞLIYOR ===")
    
    api_key = _cfg("POLY_BUILDER_KEY")
    api_secret = _cfg("POLY_BUILDER_SECRET")
    passphrase = _cfg("POLY_BUILDER_PASSPHRASE")
    eoa = _cfg("POLY_ADDRESS")
    proxy = _cfg("FUNDER_ADDRESS")

    if not all([api_key, api_secret, passphrase]):
        log.error("Builder anahtarları eksik!")
        return

    timestamp = str(int(time.time()))
    method = "POST"
    path = "/submit"
    
    # Boş ama geçerli şemaya sahip bir test gövdesi
    payload = {
        "data": "0x",
        "from": eoa,
        "metadata": "",
        "nonce": "0",
        "proxyWallet": proxy,
        "signature": "0x",
        "to": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
        "type": "EOA"
    }
    
    body = json.dumps(payload, separators=(',', ':'))
    # ÖNEMLİ: Message formatını kontrol et (Timestamp + Method + Path + Body)
    message = f"{timestamp}{method}{path}{body}"
    sig = hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    headers = {
        "POLY-BUILDER-API-KEY": api_key,
        "POLY-BUILDER-SIGNATURE": sig,
        "POLY-BUILDER-TIMESTAMP": timestamp,
        "POLY-BUILDER-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
        "User-Agent": "Polymarket-Builder-Test/1.0"
    }

    try:
        log.info(f"İstek gönderiliyor: {RELAYER_URL}")
        resp = requests.post(RELAYER_URL, json=payload, headers=headers, timeout=15)
        
        log.info(f"DÖNEN KOD: {resp.status_code}")
        log.info(f"SUNUCU YANITI: {resp.text}")
        
        if resp.status_code == 401:
            log.warning("Hala 401 alıyoruz. İmza formatı veya anahtarlar hatalı.")
        elif resp.status_code == 400:
            log.info("✅ BAŞARI: Sunucu bizi tanıdı ama 'data' geçersiz dedi. Yetki TAMAM!")
            
    except requests.exceptions.SSLError as e:
        log.error(f"SSL Hatası: {e}")
    except requests.exceptions.ConnectionError as e:
        log.error(f"Bağlantı Hatası: Sunucuya ulaşılamıyor. URL hatalı olabilir: {e}")
    except Exception as e:
        log.error(f"Beklenmeyen Hata: {type(e).__name__} - {e}")

if __name__ == "__main__":
    test_auth_v2()
    
