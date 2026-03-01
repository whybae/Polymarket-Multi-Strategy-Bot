import hmac, hashlib, time, requests, json, os, logging
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger("ManualRelayer")

def run_manual_gasless():
    # 1. Variables
    k = os.environ.get("POLY_BUILDER_KEY", "").strip()
    s = os.environ.get("POLY_BUILDER_SECRET", "").strip()
    p = os.environ.get("POLY_BUILDER_PASSPHRASE", "").strip()
    pk = os.environ.get("POLY_PRIVATE_KEY", "").strip()
    pw = os.environ.get("FUNDER_ADDRESS", "").strip()

    log.info("--- DÖKÜMAN UYUMLU MANUEL GASLESS OPERASYONU ---")

    try:
        # 2. Pozisyonu al (Dinamik)
        r_pos = requests.get(f"https://data-api.polymarket.com/positions?user={pw}&limit=1")
        cid = r_pos.json()[0]['conditionId']
        
        # 3. Payload Hazırlığı (Dökümandaki gibi)
        w3 = Web3()
        # Redeem Calldata
        data_hex = w3.eth.contract(address=Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"), abi=[{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}]).encode_abi("redeemPositions", [Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"), b"\x00" * 32, bytes.fromhex(cid[2:].zfill(64)), [1, 2]])
        
        # EOA Signature
        eoa_sig = Account.from_key(pk).sign_message(encode_defunct(primitive=Web3.keccak(bytes.fromhex(data_hex.removeprefix("0x"))))).signature.hex()

        payload = {
            "data": data_hex,
            "from": Web3.to_checksum_address(Account.from_key(pk).address),
            "metadata": "",
            "nonce": int(time.time() * 1000), # Döküman: "increment for each request"
            "proxyWallet": Web3.to_checksum_address(pw),
            "signature": eoa_sig if eoa_sig.startswith("0x") else "0x"+eoa_sig,
            "to": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
            "type": "EOA"
        }
        
        # Döküman: "Separators ensure no whitespace"
        body_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        
        # 4. HMAC SIGNATURE (DÖKÜMANDAKİ FORMÜL)
        timestamp = str(int(time.time()))
        method = "POST"
        path = "/submit"
        
        # Formül: timestamp + method + path + body
        sig_message = f"{timestamp}{method}{path}{body_str}"
        signature = hmac.new(s.encode(), sig_message.encode(), hashlib.sha256).hexdigest()

        # 5. Gönderim
        headers = {
            "POLY-API-KEY": k,
            "POLY-SIGNATURE": signature,
            "POLY-TIMESTAMP": timestamp,
            "POLY-PASSPHRASE": p,
            "Content-Type": "application/json"
        }

        log.info(f"Redeeming: {cid}")
        resp = requests.post("https://relayer-v2.polymarket.com/submit", data=body_str, headers=headers)
        
        log.info(f"SONUÇ: {resp.status_code} - {resp.text}")

    except Exception as e:
        log.error(f"Hata: {e}")

if __name__ == "__main__":
    run_manual_gasless()
