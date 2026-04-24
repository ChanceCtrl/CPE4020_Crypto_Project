"""
DePIN Validator Test Client
============================
Sends randomized sensor readings to your validator node to exercise the
consensus pipeline.  Some readings are intentionally malformed so you can
observe BLOCKED votes alongside APPROVE votes.

Usage:
    python test_client.py

Requirements:
    pip install requests rsa

Configuration:
    Edit the constants below to match your setup before running.
"""

import json
import random
import time
import hashlib

import requests
import rsa

# ---------------------------------------------------------------------------
# Configuration — edit these before running
# ---------------------------------------------------------------------------

VALIDATOR_URL = "http://localhost:4020"   # Primary validator endpoint

# Load pre-generated keypairs from files produced by keygen.py
def _load_wallet(name):
    try:
        pub  = rsa.PublicKey.load_pkcs1(open(f"{name}_public.pem",  "rb").read())
        priv = rsa.PrivateKey.load_pkcs1(open(f"{name}_private.pem", "rb").read())
        print(f"Loaded keypair: {name}")
        return {"pub": pub, "priv": priv}
    except FileNotFoundError as e:
        print(f"ERROR: {e} — run keygen.py first")
        raise SystemExit(1)

WALLETS = {
    "walletA": _load_wallet("walletA"),
    "walletB": _load_wallet("walletB"),
}

# How many test transactions to send
NUM_TRANSACTIONS = 20

# Seconds between each send (0 = fire as fast as possible)
SEND_INTERVAL = 1.5

# Probability that a given reading will be deliberately invalid (0.0 – 1.0)
INVALID_RATE = 0.25

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pem_string(pub_key: rsa.PublicKey) -> str:
    return pub_key.save_pkcs1().decode().replace("\r\n", "\n").strip()


def sign_payload(data: dict, priv_key: rsa.PrivateKey) -> str:
    """Sign the canonical JSON of data (minus the signature field)."""
    payload = {k: v for k, v in data.items() if k != "signature"}
    message_bytes = json.dumps(payload, sort_keys=True).encode()
    sig_bytes = rsa.sign(message_bytes, priv_key, "SHA-256")
    return sig_bytes.hex()


def make_valid_reading(wallet_name: str) -> dict:
    wallet = WALLETS[wallet_name]
    data = {
        "walletKey": pem_string(wallet["pub"]),
        "timeStamp": int(time.time()),
        "kwh": round(random.uniform(1, 199), 2),       # valid range: (0, 200)
        "priceperkwh": round(random.uniform(0.05, 0.50), 4),
        "signature": "",
    }
    data["signature"] = sign_payload(data, wallet["priv"])
    return data


def make_invalid_reading(fault_type: str) -> dict:
    """Return a reading with a deliberate fault."""
    wallet_name = random.choice(list(WALLETS.keys()))
    wallet = WALLETS[wallet_name]

    data = {
        "walletKey": pem_string(wallet["pub"]),
        "timeStamp": int(time.time()),
        "kwh": round(random.uniform(1, 199), 2),
        "priceperkwh": round(random.uniform(0.05, 0.50), 4),
        "signature": "",
    }

    if fault_type == "bad_kwh":
        data["kwh"] = random.choice([-5, 0, 250, 999])   # out of range
    elif fault_type == "bad_price":
        data["priceperkwh"] = random.choice([-0.1, 0])   # non-positive
    elif fault_type == "bad_wallet":
        data["walletKey"] = "UNKNOWN_WALLET_XYZ"          # not registered
    elif fault_type == "bad_signature":
        data["signature"] = sign_payload(data, wallet["priv"])
        # Tamper with the kwh AFTER signing so the signature is invalid
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

stats = {"sent": 0, "valid": 0, "invalid": 0, "errors": 0}

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
print("Test run complete.")
print(f"  Sent:    {stats['sent'] + stats['valid'] + stats['invalid']}")
print(f"  Valid:   {stats['valid']}")
print(f"  Invalid: {stats['invalid']}")
print(f"  Errors:  {stats['errors']}")
print("=" * 60)
print(f"\nFetch results:  {VALIDATOR_URL}/viewledger")
print(f"Wallets:        {VALIDATOR_URL}/viewledger/wallets")
print(f"Blockchain:     {VALIDATOR_URL}/viewledger/blockchain")