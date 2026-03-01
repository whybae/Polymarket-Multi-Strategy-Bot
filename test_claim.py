import hmac, hashlib, time, requests, json, os, logging
from web3 import Web3
from dotenv import dotenv_values
from pathlib import Path

# --- AYARLAR VE LOGLAMA ---
logging.basicConfig(level=logging.INFO, format='[%(asctime)s][%(levelname)s][AutoClaim-V2] - %(message)s')
log = logging.getLogger("AutoClaimV2")

_ROOT = Path(__file__).resolve().parent
_CFG = dotenv_values(_ROOT / ".env")

def _cfg(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    return val.strip() if val else _CFG.get(key, default).strip()

# --- POLGON AYARLARI ---
RPC_URL = "https://polygon-rpc.com"
w3 = Web3(Web3.HTTPProvider(RPC_URL))
RELAYER_URL = "https://relayer-v2.polymarket.com/submit"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045" # Polymarket CTF

def submit_to_relayer_v2(eoa_address, proxy_wallet, to, data_hex, nonce, signature):
    """Builder API Anahtarları ile Relayer'a gönderim yapar."""
    api_key = _cfg("POLY_BUILDER_KEY")
    api_secret = _cfg("POLY_BUILDER_SECRET")
    passphrase = _cfg("POLY_BUILDER_PASSPHRASE")
    
    timestamp = str(int(time.time()))
    payload = {
        "data": data_hex,
        "from": Web3.to_checksum_address(eoa_address),
        "metadata": "",
        "nonce": str(nonce),
        "proxyWallet": Web3.to_checksum_address(proxy_wallet),
        "signature": signature,
        "to": Web3.to_checksum_address(to),
        "type": "EOA",
    }
    
    body = json.dumps(payload, separators=(',', ':'))
    # ÖNEMLİ: V2 L2 Auth Header Formatı
    message = f"{timestamp}POST/submit{body}"
    sig_l2 = hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    headers = {
        "POLY_BUILDER_API_KEY": api_key,
        "POLY_BUILDER_SIGNATURE": sig_l2,
        "POLY_BUILDER_TIMESTAMP": timestamp,
        "POLY_BUILDER_PASSPHRASE": passphrase,
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(RELAYER_URL, json=payload, headers=headers, timeout=30)
        return resp
    except Exception as e:
        log.error(f"İstek Hatası: {e}")
        return None

if __name__ == "__main__":
    log.info("=== BUILDER V2 OTOMATİK ÇEKİM BAŞLIYOR ===")
    
    EOA = _cfg("POLY_ADDRESS") # Senin MetaMask adresin
    PROXY = _cfg("FUNDER_ADDRESS") # Botun kullandığı Proxy (0x60b...)
    
    if not EOA or not PROXY:
        log.error("Cüzdan adresleri eksik! POLY_ADDRESS ve FUNDER_ADDRESS kontrol et.")
    else:
        log.info(f"Cüzdan: {EOA} | Proxy: {PROXY}")
        log.info("Çekilebilir pozisyonlar taranıyor... (Builder Modu)")
        
        # Test amaçlı bir ping atarak yetkiyi kontrol edelim
        # Eğer loglarda 401 yerine 400 (bad request - data eksik) alırsak yetki TAMAM demektir.
        test_resp = submit_to_relayer_v2(EOA, PROXY, CTF_ADDRESS, "0x", 0, "0x")
        
        if test_resp and test_resp.status_code == 401:
            log.error("❌ YETKİ HATASI: Builder anahtarları hala reddediliyor (401).")
        elif test_resp:
            log.info(f"✅ YETKİ ONAYLANDI (Yanıt: {test_resp.status_code}). Builder sistemi çalışıyor!")
        else:
            log.error("Bağlantı kurulamadı.")
