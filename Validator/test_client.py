"""
DePIN Validator Test Client — ECDSA P-256 edition
==================================================
Sends randomized sensor readings to your validator node to exercise the
consensus pipeline. Some readings are intentionally malformed so you can
observe BLOCKED votes alongside APPROVE votes.

Usage:
    pip install requests ecdsa --break-system-packages
    python keygen.py        # only needs to be run once
    python test_client.py

Configuration:
    Edit the constants below to match your setup before running.
"""

import json
import random
import time
import hashlib
from pathlib import Path

import requests
from ecdsa import SigningKey, NIST256p
from ecdsa.util import sigencode_string

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VALIDATOR_URL = "http://localhost:4020"   # Primary validator endpoint

# Load pre-generated keypairs from files produced by keygen.py
def _load_wallet(name):
    try:
        priv = SigningKey.from_string(
            Path(f"{name}.priv").read_bytes(),
            curve=NIST256p,
        )
        pub_hex = Path(f"{name}.pub.hex").read_text().strip().lower()
        print(f"Loaded keypair: {name}")
        return {"priv": priv, "pub_hex": pub_hex}
    except FileNotFoundError as e:
        print(f"ERROR: {e} — run keygen.py first")
        raise SystemExit(1)

WALLETS = {
    "walletA": _load_wallet("walletA"),
    "walletB": _load_wallet("walletB"),
}

NUM_TRANSACTIONS = 20
SEND_INTERVAL    = 1.5
INVALID_RATE     = 0.25

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sign_payload(data: dict, priv_key: SigningKey) -> str:
    """Sign canonical JSON of data (minus the signature field). Returns hex."""
    payload = {k: v for k, v in data.items() if k != "signature"}
    canonical = json.dumps(payload, sort_keys=True).encode()
    sig_bytes = priv_key.sign(canonical, hashfunc=hashlib.sha256, sigencode=sigencode_string)
    return sig_bytes.hex()


def make_valid_reading(wallet_name: str) -> dict:
    wallet = WALLETS[wallet_name]
    data = {
        "walletKey":   wallet["pub_hex"],
        "timeStamp":   int(time.time()),
        "kwh":         round(random.uniform(1, 199), 2),
        "priceperkwh": round(random.uniform(0.05, 0.50), 4),
        "signature":   "",
    }
    data["signature"] = sign_payload(data, wallet["priv"])
    return data


def make_invalid_reading(fault_type: str) -> dict:
    """Return a reading with a deliberate fault."""
    wallet_name = random.choice(list(WALLETS.keys()))
    wallet = WALLETS[wallet_name]

    data = {
        "walletKey":   wallet["pub_hex"],
        "timeStamp":   int(time.time()),
        "kwh":         round(random.uniform(1, 199), 2),
        "priceperkwh": round(random.uniform(0.05, 0.50), 4),
        "signature":   "",
    }

    if fault_type == "bad_kwh":
        data["kwh"] = random.choice([-5, 0, 250, 999])
    elif fault_type == "bad_price":
        data["priceperkwh"] = random.choice([-0.1, 0])
    elif fault_type == "bad_wallet":
        data["walletKey"] = "00" * 64   # 128 hex chars but not registered
    elif fault_type == "bad_signature":
        data["signature"] = sign_payload(data, wallet["priv"])
        # Tamper with kwh AFTER signing so signature is invalid
        data["kwh"] = round(data["kwh"] + 100, 2)
        return data

    data["signature"] = sign_payload(data, wallet["priv"])
    return data


def send(data: dict) -> int:
    """POST data to /publishdata. Returns HTTP status code."""
    try:
        r = requests.post(f"{VALIDATOR_URL}/publishdata", json=data, timeout=5)
        return r.status_code
    except requests.exceptions.RequestException as e:
        print(f"  Request failed: {e}")
        return -1


# ---------------------------------------------------------------------------
# Main test loop
# ---------------------------------------------------------------------------

FAULT_TYPES = ["bad_kwh", "bad_price", "bad_wallet", "bad_signature"]

print("=" * 60)
print(f"Sending {NUM_TRANSACTIONS} transactions to {VALIDATOR_URL}")
print(f"  Invalid rate: {INVALID_RATE*100:.0f}%   Interval: {SEND_INTERVAL}s")
print("=" * 60)

stats = {"valid": 0, "invalid": 0, "errors": 0}

for i in range(1, NUM_TRANSACTIONS + 1):
    is_invalid = random.random() < INVALID_RATE

    if is_invalid:
        fault = random.choice(FAULT_TYPES)
        data = make_invalid_reading(fault)
        label = f"INVALID ({fault})"
        stats["invalid"] += 1
    else:
        wallet_name = random.choice(list(WALLETS.keys()))
        data = make_valid_reading(wallet_name)
        label = f"VALID   ({wallet_name})"
        stats["valid"] += 1

    tx_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]
    status = send(data)
    if status == -1:
        stats["errors"] += 1

    print(f"[{i:02d}/{NUM_TRANSACTIONS}] {label}  kwh={data['kwh']:6.2f}  "
          f"price={data['priceperkwh']:.4f}  hash={tx_hash}...  HTTP {status}")

    if SEND_INTERVAL > 0:
        time.sleep(SEND_INTERVAL)

print("\n" + "=" * 60)
print(f"  Valid:   {stats['valid']}")
print(f"  Invalid: {stats['invalid']}")
print(f"  Errors:  {stats['errors']}")
print("=" * 60)
print(f"\nFetch results:  {VALIDATOR_URL}/viewledger")
print(f"Wallets:        {VALIDATOR_URL}/viewledger/wallets")
print(f"Blockchain:     {VALIDATOR_URL}/viewledger/blockchain")