def submit_to_relayer_v2(eoa_address, proxy_wallet, to, data_hex, nonce, signature):
    api_key = _cfg("POLY_BUILDER_KEY")
    api_secret = _cfg("POLY_BUILDER_SECRET")
    passphrase = _cfg("POLY_BUILDER_PASSPHRASE")
    
    timestamp = str(int(time.time()))
    method = "POST"
    path = "/submit"
    
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
    
    # Polymarket V2 Kesin İmza Formatı: timestamp + method + path + body
    body = json.dumps(payload, separators=(',', ':'))
    message = f"{timestamp}{method}{path}{body}"
    
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
        # Hata mesajını daha detaylı görelim
        if resp.status_code != 200:
            log.warning(f"Sunucu Yanıtı: {resp.status_code} - {resp.text}")
        return resp
    except Exception as e:
        log.error(f"Gerçek Bağlantı Hatası: {e}")
        return None
