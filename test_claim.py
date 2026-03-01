def _cfg(key: str) -> str:
    # Hem normal hem de başında/sonunda boşluk olma ihtimaline karşı temizleyerek oku
    val = os.environ.get(key, "")
    if not val:
        # Alternatif olarak .env dosyasına da bak
        val = _CFG.get(key, "")
    return val.strip()

def test_auth_v2():
    log.info("=== DERİN TEŞHİS BAŞLIYOR ===")
    
    api_key = _cfg("POLY_BUILDER_KEY")
    api_secret = _cfg("POLY_BUILDER_SECRET")
    passphrase = _cfg("POLY_BUILDER_PASSPHRASE")

    # Hangi anahtarın eksik olduğunu loglara yazdıralım
    if not api_key: log.error("Eksik: POLY_BUILDER_KEY")
    if not api_secret: log.error("Eksik: POLY_BUILDER_SECRET")
    if not passphrase: log.error("Eksik: POLY_BUILDER_PASSPHRASE")

    if not all([api_key, api_secret, passphrase]):
        return
    # ... geri kalan kod aynı
