import hmac, hashlib, time, requests, json, os, logging
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger("ClobAuth")

def run_documented_claim():
    # Variables
    k = os.environ.get("POLY_BUILDER_KEY", "").strip()
    s = os.environ.get("POLY_BUILDER_SECRET", "").strip()
    p = os.environ.get("POLY_BUILDER_PASSPHRASE", "").strip()
    pk = os.environ.get("POLY_PRIVATE_KEY", "").strip()
    pw = os.environ.get("FUNDER_ADDRESS", "").strip()

    log.info("--- DÖKÜMAN ONAYLI L2 AUTH TESTİ ---")

    try:
        # 1. Pozisyonu yakala
        r_pos = requests.get(f"https://data-api.polymarket.com/positions?user={pw}&limit=1")
        cid = r_pos.json()[0]['conditionId']
        
        # 2. Calldata (Redeem Positions)
        w3 = Web3()
        contract_addr = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        data = w3.eth.contract(address=contract_addr, abi=[{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}]).encode_abi("redeemPositions", ["0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", b"\x00"*32, bytes.fromhex(cid[2:].zfill(64)), [1, 2]])

        # 3. EOA İmzası (L1)
        eoa_sig = Account.from_key(pk).sign_message(encode_defunct(primitive=Web3.keccak(bytes.fromhex(data.removeprefix("0x"))))).signature.hex()

        # 4. Payload (Dökümandaki SignatureType Tablosuna göre)
        payload = {
            "data": data,
            "from": Web3.to_checksum_address(Account.from_key(pk).address),
            "metadata": "",
            "nonce": int(time.time() * 1000),
            "proxyWallet": Web3.to_checksum_address(pw),
            "signature": eoa_sig if eoa_sig.startswith("0x") else "0x"+eoa_sig,
            "to": contract_addr,
            "type": "GNOSIS_SAFE" # Döküman: "Most common for returning users"
        }
        
        body_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        timestamp = str(int(time.time()))
        
        # 5. L2 HMAC İmza (timestamp + method + path + body)
        msg = f"{timestamp}POST/submit{body_str}"
        sig_l2 = hmac.new(s.encode(), msg.encode(), hashlib.sha256).hexdigest()

        headers = {
            "POLY-API-KEY": k,
            "POLY-SIGNATURE": sig_l2,
            "POLY-TIMESTAMP": timestamp,
            "POLY-PASSPHRASE": p,
            "Content-Type": "application/json"
        }

        log.info(f"Relayer'a gönderiliyor (Type: GNOSIS_SAFE)...")
        resp = requests.post("https://relayer-v2.polymarket.com/submit", data=body_str, headers=headers)
        
        log.info(f"YANIT: {resp.status_code} - {resp.text}")

    except Exception as e:
        log.error(f"Sistem Hatası: {e}")

if __name__ == "__main__":
    run_documented_claim()
