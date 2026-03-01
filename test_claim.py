import hmac, hashlib, time, requests, json, os, logging
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger("PolymarketDocs")

def run_doc_compliant_test():
    # Variables
    k = os.environ.get("POLY_BUILDER_KEY", "").strip()
    s = os.environ.get("POLY_BUILDER_SECRET", "").strip()
    p = os.environ.get("POLY_BUILDER_PASSPHRASE", "").strip()
    pk = os.environ.get("POLY_PRIVATE_KEY", "").strip()
    pw = os.environ.get("FUNDER_ADDRESS", "").strip()

    try:
        # 1. Pozisyonu al
        r_pos = requests.get(f"https://data-api.polymarket.com/positions?user={pw}&limit=1")
        cid = r_pos.json()[0]['conditionId']
        
        # 2. Calldata (Dökümandaki yapıya uygun)
        w3 = Web3()
        contract = w3.eth.contract(address=Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"), abi=[{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}])
        data_hex = contract.encode_abi("redeemPositions", args=[Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"), b"\x00" * 32, bytes.fromhex(cid[2:].zfill(64)), [1, 2]])

        # 3. EOA İmzası
        msg_hash = Web3.keccak(bytes.fromhex(data_hex.removeprefix("0x")))
        eoa_sig = Account.from_key(pk).sign_message(encode_defunct(primitive=msg_hash)).signature.hex()
        if not eoa_sig.startswith("0x"): eoa_sig = "0x" + eoa_sig

        # 4. REQUEST BODY (Döküman: "nonce should be a number")
        payload = {
            "data": data_hex,
            "from": Web3.to_checksum_address(Account.from_key(pk).address),
            "metadata": "",
            "nonce": int(time.time()), # Dökümana uygun integer nonce
            "proxyWallet": Web3.to_checksum_address(pw),
            "signature": eoa_sig,
            "to": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
            "type": "EOA"
        }
        
        # Döküman: "Strict JSON, no spaces"
        body_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        
        # 5. AUTH SIGNATURE (Döküman: timestamp + method + path + body)
        timestamp = str(int(time.time()))
        message = f"{timestamp}POST/submit{body_str}"
        sig_l2 = hmac.new(s.encode(), message.encode(), hashlib.sha256).hexdigest()

        headers = {
            "POLY-API-KEY": k,
            "POLY-SIGNATURE": sig_l2,
            "POLY-TIMESTAMP": timestamp,
            "POLY-PASSPHRASE": p,
            "Content-Type": "application/json"
        }

        log.info(f"Döküman uyumlu istek gönderiliyor... (Nonce: {payload['nonce']})")
        resp = requests.post("https://relayer-v2.polymarket.com/submit", data=body_str, headers=headers)
        
        log.info(f"SONUÇ: {resp.status_code} - {resp.text}")

    except Exception as e:
        log.error(f"Hata: {e}")

if __name__ == "__main__":
    run_doc_compliant_test()
