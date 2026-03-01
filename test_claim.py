import hmac, hashlib, time, requests, json, os, logging
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger("FinalPush")

def run_final_test():
    # 1. Variables
    k, s, p = os.environ.get("POLY_BUILDER_KEY",""), os.environ.get("POLY_BUILDER_SECRET",""), os.environ.get("POLY_BUILDER_PASSPHRASE","")
    pk = os.environ.get("POLY_PRIVATE_KEY","")
    pw = os.environ.get("FUNDER_ADDRESS","")

    log.info("--- SON ŞANS OPERASYONU ---")

    try:
        # Pozisyon bul
        r = requests.get(f"https://data-api.polymarket.com/positions?user={pw}&limit=1")
        cid = r.json()[0]['conditionId']
        
        # Calldata ve İmza
        w3 = Web3()
        cid_bytes = bytes.fromhex(cid[2:].zfill(64))
        data_hex = w3.eth.contract(address=Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"), abi=[{"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[],"stateMutability":"nonpayable"}]).encode_abi("redeemPositions", args=[Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"), b"\x00" * 32, cid_bytes, [1, 2]])
        
        account = Account.from_key(pk)
        eoa_sig = account.sign_message(encode_defunct(primitive=Web3.keccak(bytes.fromhex(data_hex.removeprefix("0x"))))).signature.hex()

        # PAYLOAD: Nonce'u integer (sayı) yapıyoruz
        payload = {
            "data": data_hex,
            "from": Web3.to_checksum_address(account.address),
            "metadata": "",
            "nonce": int(time.time()), # TIRNAKSIZ SAYI
            "proxyWallet": Web3.to_checksum_address(pw),
            "signature": eoa_sig if eoa_sig.startswith("0x") else "0x"+eoa_sig,
            "to": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
            "type": "EOA"
        }
        
        body_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        ts = str(int(time.time()))
        
        # İmza Mesajı (En sade hali)
        msg = f"{ts}POST/submit{body_str}"
        sig = hmac.new(s.encode(), msg.encode(), hashlib.sha256).hexdigest()

        headers = {
            "POLY-API-KEY": k,
            "POLY-SIGNATURE": sig,
            "POLY-TIMESTAMP": ts,
            "POLY-PASSPHRASE": p,
            "Content-Type": "application/json"
        }

        resp = requests.post("https://relayer-v2.polymarket.com/submit", data=body_str, headers=headers)
        log.info(f"SONUÇ: {resp.status_code} - {resp.text}")

    except Exception as e:
        log.error(f"SİSTEM HATASI: {e}")

if __name__ == "__main__":
    run_final_test()
