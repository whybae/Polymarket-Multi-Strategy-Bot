import os
from web3 import Web3

def debug_safe_permissions():
    w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
    proxy_address = os.environ.get("FUNDER_ADDRESS")
    my_address = "0x4281191A76590706b34bB57b8B832e0F94c7B9cE" # Senin EOA adresin
    
    # Safe Kontratı (Basit Owner Sorgusu)
    abi = [{"name":"isOwner","type":"function","inputs":[{"name":"owner","type":"address"}],"outputs":[{"name":"","type":"bool"}],"stateMutability":"view"}]
    contract = w3.eth.contract(address=Web3.to_checksum_address(proxy_address), abi=abi)
    
    try:
        is_owner = contract.functions.isOwner(Web3.to_checksum_address(my_address)).call()
        print(f"SORGULAMA: {my_address} adresi, {proxy_address} cüzdanının sahibi mi?")
        print(f"CEVAP: {'EVET ✅' if is_owner else 'HAYIR ❌'}")
    except Exception as e:
        print(f"HATA: Bu cüzdan bir 'Safe' kontratı olmayabilir: {e}")

if __name__ == "__main__":
    debug_safe_permissions()
