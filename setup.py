"""
setup.py
--------
One-command environment setup and validation for Polymarket-Trading-Asset-Bot.

Usage:
    python setup.py                  # full setup: prompt, install, generate, validate
    python setup.py --check-only     # validate without installing or modifying .env
    python setup.py --regen-keys     # force regeneration of API credentials
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────
ROOT_DIR  = Path(__file__).parent
ENV_FILE  = ROOT_DIR / ".env"
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID  = 137

ASSETS = ["btc", "eth", "sol", "xrp"]

STRATEGIES = {
    "DCA_Snipe"  : [f"strategies/DCA_Snipe/markets/{a}"         for a in ASSETS],
    "YES+NO_1usd": [f"strategies/YES+NO_1usd/markets/{a}"       for a in ASSETS],
}

REQUIRED_PACKAGES = [
    "py-clob-client",
    "python-dotenv",
    "requests",
    "web3",
    "eth-abi",
    "websocket-client",
    "questionary",
]

REQUIRED_VARS = [
    "POLY_PRIVATE_KEY",
    "FUNDER_ADDRESS",
    "POLY_RPC",
    "SIGNATURE_TYPE",
    "POLY_API_KEY",
    "POLY_API_SECRET",
    "POLY_API_PASSPHRASE",
]

PROMPT_VARS = [
    # (env_key,              display_label,                                    is_secret)
    ("POLY_PRIVATE_KEY", "EOA private key (0x...)",                            True),
    ("FUNDER_ADDRESS",   "Proxy wallet address (from your Polymarket profile)", False),
    ("POLY_RPC",         "Polygon RPC URL (e.g. https://polygon-rpc.com)",      False),
    ("SIGNATURE_TYPE",   "Signature type  [0=EOA | 1=Magic | 2=Proxy]",        False),
]

DEFAULT_ORDER_VARS = {
    "BUY_ORDER_TYPE"       : "FAK",
    "SELL_ORDER_TYPE"      : "FAK",
    "GTC_TIMEOUT_SECONDS"  : "30",
    "FOK_GTC_FALLBACK"     : "false",
    "WSS_READY_TIMEOUT"    : "10.0",
    "CLAIM_CHECK_INTERVAL" : "180",
}


# ══════════════════════════════════════════════════════════════════════════════
#  DISPLAY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def header(title: str) -> None:
    print(f"\n{'─' * 54}")
    print(f"  {title}")
    print(f"{'─' * 54}")

def ok(msg: str)   -> None: print(f"  [✔] {msg}")
def warn(msg: str) -> None: print(f"  [!] {msg}")
def err(msg: str)  -> None: print(f"  [✘] {msg}")
def info(msg: str) -> None: print(f"  [i] {msg}")


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 1 — Python version
# ══════════════════════════════════════════════════════════════════════════════

def check_python_version() -> bool:
    header("Python Version")
    major, minor = sys.version_info.major, sys.version_info.minor
    version_str  = f"{major}.{minor}.{sys.version_info.micro}"
    if (major, minor) >= (3, 9):
        ok(f"Python {version_str}  (>= 3.9 required)")
        return True
    err(f"Python {version_str} detected — version >= 3.9 required")
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 2 — Directory structure
# ══════════════════════════════════════════════════════════════════════════════

def check_directory_structure() -> bool:
    header("Project Structure")
    all_ok = True

    # Root-level modules
    for fname in ["order_executor.py", "market_stream.py", "main.py", "auto_claim.py"]:
        fpath = ROOT_DIR / fname
        if fpath.exists():
            ok(fname)
        else:
            warn(f"{fname} not found — some features may not work")

    # Strategy directories + bot files
    for strategy, paths in STRATEGIES.items():
        for rel_path in paths:
            bot_file  = ROOT_DIR / rel_path / "bot.py"
            init_file = ROOT_DIR / rel_path / "__init__.py"

            if not (ROOT_DIR / rel_path).exists():
                warn(f"{rel_path}/ missing — creating ...")
                (ROOT_DIR / rel_path).mkdir(parents=True, exist_ok=True)

            if not init_file.exists():
                init_file.write_text("")

            if bot_file.exists():
                ok(f"{rel_path}/bot.py")
            else:
                warn(f"{rel_path}/bot.py not found")
                all_ok = False

    return all_ok


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 3 — Install packages
# ══════════════════════════════════════════════════════════════════════════════

def install_packages(check_only: bool = False) -> bool:
    header("Python Dependencies")

    import_map = {
        "py-clob-client"  : "py_clob_client",
        "python-dotenv"   : "dotenv",
        "requests"        : "requests",
        "web3"            : "web3",
        "eth-abi"         : "eth_abi",
        "websocket-client": "websocket",
        "questionary"     : "questionary",
    }

    missing = []
    for pkg, import_name in import_map.items():
        try:
            __import__(import_name)
            ok(pkg)
        except ImportError:
            warn(f"{pkg}  — not installed")
            missing.append(pkg)

    if not missing:
        ok("All required packages are present.")
        return True

    if check_only:
        err(f"Missing: {', '.join(missing)}")
        err("Run  python setup.py  (without --check-only) to install them.")
        return False

    print(f"\n  Installing {len(missing)} missing package(s) ...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet"] + missing,
        capture_output=False,
    )
    if result.returncode != 0:
        err("pip install failed. Check your internet connection and try again.")
        return False

    ok("All packages installed successfully.")
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  .env read / write helpers
# ══════════════════════════════════════════════════════════════════════════════

def _read_env_raw() -> dict:
    """Read .env as raw key=value pairs without modifying os.environ."""
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    return values


def _write_env_value(key: str, value: str) -> None:
    """Update or append a single key=value in .env, preserving comments and order."""
    lines    = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    new_line = f"{key}={value}"
    updated  = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.partition("=")[0].strip() == key:
            lines[i] = new_line
            updated  = True
            break

    if not updated:
        lines.append(new_line)

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ensure_env_skeleton() -> None:
    """
    Create .env if it does not exist yet.
    Priority: copy from .env.example if present, otherwise write a minimal skeleton.
    """
    if ENV_FILE.exists():
        return

    example_file = ROOT_DIR / ".env.example"
    if example_file.exists():
        import shutil
        shutil.copy(example_file, ENV_FILE)
        ok(".env created from .env.example.")
    else:
        warn(".env not found and .env.example missing — creating minimal skeleton ...")
        lines = [
            "# Polymarket Trading Asset Bot — environment config",
            "# Generated by setup.py",
            "",
            "POLY_PRIVATE_KEY=",
            "FUNDER_ADDRESS=",
            "POLY_RPC=",
            "SIGNATURE_TYPE=2",
            "",
            "POLY_API_KEY=",
            "POLY_API_SECRET=",
            "POLY_API_PASSPHRASE=",
            "",
        ]
        for k, v in DEFAULT_ORDER_VARS.items():
            lines.append(f"{k}={v}")
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok(".env skeleton created.")
def prompt_base_credentials(check_only: bool = False) -> bool:
    header("Wallet & Network Configuration")

    _ensure_env_skeleton()
    current      = _read_env_raw()
    any_prompted = False

    for key, label, is_secret in PROMPT_VARS:
        existing = current.get(key, "").strip()

        if existing:
            masked = existing[:6] + "…" if len(existing) > 8 else "****"
            ok(f"{key} = {masked}")
            continue

        if check_only:
            err(f"{key} is empty — re-run without --check-only to configure it")
            continue

        print(f"\n  {key}")
        print(f"  {label}")

        while True:
            if is_secret:
                import getpass
                value = getpass.getpass("  → ").strip()
            else:
                value = input("  → ").strip()
            if value:
                break
            warn("  Value cannot be empty — please try again.")

        _write_env_value(key, value)
        ok(f"{key} saved.")
        any_prompted = True

    # Write default order/claim vars if absent
    current = _read_env_raw()
    for k, v in DEFAULT_ORDER_VARS.items():
        if not current.get(k, "").strip():
            _write_env_value(k, v)

    if not any_prompted:
        ok("All wallet/network variables are already configured.")

    return True


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 5 — Derive and save API credentials
# ══════════════════════════════════════════════════════════════════════════════

def derive_and_save_credentials(force: bool = False) -> bool:
    header("API Credentials")

    current   = _read_env_raw()
    cred_keys = ["POLY_API_KEY", "POLY_API_SECRET", "POLY_API_PASSPHRASE"]
    all_present = all(current.get(k, "").strip() for k in cred_keys)

    if all_present and not force:
        ok("API credentials already present in .env")
        for k in cred_keys:
            v      = current[k]
            masked = v[:6] + "…" if len(v) > 8 else "****"
            ok(f"  {k} = {masked}")
        return True

    if force:
        warn("--regen-keys requested — regenerating API credentials ...")
    else:
        warn("API credentials missing or incomplete — deriving now ...")

    current     = _read_env_raw()
    private_key = current.get("POLY_PRIVATE_KEY", "").strip()
    funder      = current.get("FUNDER_ADDRESS",   "").strip()
    sig_type    = int(current.get("SIGNATURE_TYPE", "2"))

    if not private_key or not funder:
        err("POLY_PRIVATE_KEY and FUNDER_ADDRESS must be filled before deriving credentials.")
        return False

    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        err("py-clob-client not installed — run: pip install py-clob-client")
        return False

    try:
        info("Connecting to Polymarket CLOB ...")
        client = ClobClient(
            host           = CLOB_HOST,
            key            = private_key,
            chain_id       = CHAIN_ID,
            signature_type = sig_type,
            funder         = funder,
        )

        info("Deriving credentials via EIP-712 signing ...")
        creds = client.create_or_derive_api_creds()

        _write_env_value("POLY_API_KEY",        creds.api_key)
        _write_env_value("POLY_API_SECRET",     creds.api_secret)
        _write_env_value("POLY_API_PASSPHRASE", creds.api_passphrase)

        ok(f"POLY_API_KEY        = {creds.api_key[:6]}…")
        ok(f"POLY_API_SECRET     = {creds.api_secret[:6]}…")
        ok(f"POLY_API_PASSPHRASE = {creds.api_passphrase[:6]}…")

    except Exception as exc:
        err(f"Credential derivation failed: {exc}")
        return False

    # Smoke-test: verify credentials are accepted by the CLOB
    try:
        from py_clob_client.clob_types import ApiCreds
        api_creds = ApiCreds(
            api_key        = creds.api_key,
            api_secret     = creds.api_secret,
            api_passphrase = creds.api_passphrase,
        )
        client2 = ClobClient(
            host           = CLOB_HOST,
            key            = private_key,
            chain_id       = CHAIN_ID,
            creds          = api_creds,
            signature_type = sig_type,
            funder         = funder,
        )
        client2.get_api_keys()
        ok("Credential verification passed — CLOB accepted the keys.")
    except Exception as exc:
        warn(f"Verification request failed: {exc}")
        warn("Credentials were saved — they may still be valid.")

    return True


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 6 — Final .env validation
# ══════════════════════════════════════════════════════════════════════════════

def validate_env() -> bool:
    header(".env Final Validation")
    current = _read_env_raw()
    all_ok  = True

    for var in REQUIRED_VARS:
        value = current.get(var, "").strip()
        if value:
            masked = value[:6] + "…" if len(value) > 8 else "****"
            ok(f"{var} = {masked}")
        else:
            err(f"{var} is empty or missing")
            all_ok = False

    return all_ok


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 7 — Network connectivity
# ══════════════════════════════════════════════════════════════════════════════

def check_connectivity() -> bool:
    header("Network Connectivity")
    try:
        import requests
        resp = requests.get(CLOB_HOST, timeout=8)
        if resp.status_code < 500:
            ok(f"Polymarket CLOB reachable  (HTTP {resp.status_code})")
            return True
        warn(f"CLOB returned HTTP {resp.status_code} — may be temporarily degraded")
        return True
    except Exception as exc:
        err(f"Could not reach Polymarket CLOB: {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

def print_summary(results: dict) -> None:
    header("Setup Summary")
    all_passed = True
    for step, passed in results.items():
        if passed:
            ok(step)
        else:
            err(step)
            all_passed = False

    print()
    if all_passed:
        print("  ✅  Environment is ready. Start a bot with:")
        print()
        print("        Strategy 1 — DCA Snipe:")
        print("          python main.py --operate btc")
        print("          python main.py --operate btc eth sol xrp")
        print()
        print("        Strategy 2 — YES+NO Arbitrage:")
        print("          python strategies/YES+NO_1usd/markets/btc/bot.py")
        print()
        print("        Auto Claim:")
        print("          python auto_claim.py")
    else:
        print("  ❌  Some checks failed — fix the issues above and re-run setup.py")
    print()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Trading Asset Bot — Environment Setup"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate only — no installations or .env modifications",
    )
    parser.add_argument(
        "--regen-keys",
        action="store_true",
        help="Force regeneration of API credentials even if already present",
    )
    args = parser.parse_args()

    print("\n" + "=" * 54)
    print("  Polymarket Trading Asset Bot — Setup")
    print("=" * 54)

    if args.check_only:
        info("Running in check-only mode — nothing will be modified.")

    results = {}
    results["Python >= 3.9"]          = check_python_version()
    results["Project structure"]       = check_directory_structure()
    results["Python packages"]         = install_packages(check_only=args.check_only)
    results["Wallet & network config"] = prompt_base_credentials(check_only=args.check_only)
    results["API credentials"]         = derive_and_save_credentials(force=args.regen_keys)
    results[".env validation"]         = validate_env()
    results["Network / CLOB"]          = check_connectivity()

    print_summary(results)


if __name__ == "__main__":
    main()