import hmac
import hashlib
import time
import requests
import json
import os
from web3 import Web3
from dotenv import dotenv_values
from pathlib import Path
import logging

# Loglama ayarları (Test sonuçlarını görmek için)
logging.basicConfig(level=logging.INFO, format='[%(asctime)s][%(levelname)s][Test] - %(message)s')
log = logging.getLogger("TestClaim")

_ROOT = Path(__file__).resolve().parent
_CFG = dotenv_values(_ROOT / ".env")

def _cfg(key: str, default: str = "") -> str:
    """Railway Variables veya .env dosyasından veri oku."""
    val = os.environ.get(key)
    if val: return val.strip()
    return _CFG.get(key, default).strip()

# --- ÖNEMLİ AYARLAR ---
RELAYER_URL = "https://relayer-v2.polymarket.com/submit" # Güncel V2 Adresi

def submit_to_relayer_v2(eoa_address: str, proxy_wallet: str, to: str, 
                         data_hex: str, nonce: int, signature: str) -> dict | None:
    """Builder API Anahtarları ile Yetkilendirilmiş Gönderim."""
    
    # 1. Builder Kimlik Bilgilerini Oku
    api_key = _cfg("POLY_BUILDER_KEY")
    api_secret = _cfg("POLY_BUILDER_SECRET")
    passphrase = _cfg("POLY_BUILDER_PASSPHRASE")
    
    if not api_key or not api_secret:
        log.error("❌ Builder Anahtarları Eksik! Railway Variables'ı kontrol et.")
        return None

    timestamp = str(int(time.time()))
    method = "POST"
    path = "/submit"

    # 2. Payload Hazırla
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
    
    # 3. L2 Builder İmzası (HMAC-SHA256)
    body = json.dumps(payload, separators=(',', ':'))
    message = f"{timestamp}{method}{path}{body}"
    sig_l2 = hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    # 4. Header'ları Oluştur
    headers = {
        "POLY_BUILDER_API_KEY": api_key,
        "POLY_BUILDER_SIGNATURE": sig_l2,
        "POLY_BUILDER_TIMESTAMP": timestamp,
        "POLY_BUILDER_PASSPHRASE": passphrase,
        "Content-Type": "application/json"
    }

    log.info(f"🚀 Relayer-V2'ye gönderiliyor... (Nonce: {nonce})")

    try:
        resp = requests.post(RELAYER_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code in (200, 201):
            log.info("✅ BAŞARILI: Relayer işlemi kabul etti!")
            return resp.json()
        else:
            log.error(f"❌ REDDEDİLDİ: HTTP {resp.status_code} - {resp.text}")
            return None
    except Exception as e:
        log.error(f"💥 HATA: İstek gönderilemedi: {e}")
        return None

# --- TEST ÇALIŞTIRICI ---
if __name__ == "__main__":
    log.info("=== Polymarket Builder Auth Test Başlıyor ===")
    
    # Test için gerekli temel bilgileri kontrol et
    p_key = _cfg("POLY_PRIVATE_KEY")
    if not p_key:
        log.error("❌ POLY_PRIVATE_KEY bulunamadı. Test yapılamaz.")
    else:
        log.info("✅ Temel yapı hazır. Railway komutunu bekliyor...")
        # Not: Gerçek bir 'claim' denemesi yapması için asıl auto_claim mantığını buraya bağlayabilirsin.
        # Şimdilik sadece altyapıyı kurduk.
