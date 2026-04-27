import time
import json
import hashlib
import threading

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

from ecdsa import VerifyingKey, BadSignatureError, NIST256p
from ecdsa.util import sigdecode_string
from hashlib import sha256

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# Shared state + lock
# ---------------------------------------------------------------------------

state_lock = threading.Lock()

def _load_known_wallets(names):
    wallets = {}
    for name in names:
        path = f"{name}.pub.hex"
        try:
            hex_key = open(path, "r").read().strip().lower()
            wallets[hex_key] = 0
            print(f"Loaded public key: {path} ({len(hex_key)} hex chars)")
        except FileNotFoundError:
            print(f"WARNING: {path} not found — run keygen.py first")
    return wallets

knownWallets = _load_known_wallets(["walletA", "walletB"])

validatorAddresses = {
    "self":       "172.20.10.4:4020",
    "validatorA": "172.20.10.2:4020",
}

pending_requests = {}
transaction_ledger = []
block_ledger = []

# Maps tx_hash -> this node's vote ("APPROVE" or "BLOCKED") for every
# transaction this node has ever seen, including already-settled ones.
# This lets /checkvote answer correctly even after a tx leaves pending_requests.
settled_votes = {}

BLOCK_SIZE = 3


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def verify_signature(public_key_hex: str, message: dict, signature_hex: str) -> bool:
    """
    Verify an ECDSA-P256 signature over the canonical JSON of the payload.

    Canonical format: json.dumps(payload_without_signature, sort_keys=True)
    with default Python separators (', ' and ': ').

    The public key is the 64-byte raw uncompressed point (X || Y) hex-encoded.
    The signature is 64 bytes (r || s) hex-encoded — same format as uECC_sign
    on the Arduino.
    """
    try:
        # Normalize hex inputs
        public_key_hex  = public_key_hex.strip().lower()
        signature_hex   = signature_hex.strip().lower()
        public_key_bytes = bytes.fromhex(public_key_hex)
        signature_bytes  = bytes.fromhex(signature_hex)

        if len(public_key_bytes) != 64:
            print(f"[verify] FAIL: pubkey is {len(public_key_bytes)} bytes, expected 64")
            return False
        if len(signature_bytes) != 64:
            print(f"[verify] FAIL: sig is {len(signature_bytes)} bytes, expected 64")
            return False

        # Build canonical JSON (matches test_client and Arduino)
        payload = {k: v for k, v in message.items() if k != "signature"}
        canonical = json.dumps(payload, sort_keys=True).encode()

        vk = VerifyingKey.from_string(public_key_bytes, curve=NIST256p)
        vk.verify(signature_bytes, canonical, hashfunc=sha256, sigdecode=sigdecode_string)
        return True

    except BadSignatureError:
        print("[verify] FAIL: bad signature")
        return False
    except Exception as e:
        print(f"[verify] FAIL: {e}")
        return False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_data(data: dict) -> bool:
    """
    Accepts either:
      - {kwh: float, priceperkwh: float}     (test_client.py)
      - {kwh_milli: int, price_micro: int}   (Arduino — integer-scaled for
                                              cross-platform JSON compatibility)
    """
    wallet_key = data.get("walletKey", "").strip().lower()
    signature  = data.get("signature")

    # Convert integer-scaled fields to floats if present
    if "kwh_milli" in data and "price_micro" in data:
        kwh   = data["kwh_milli"]  / 1000.0
        price = data["price_micro"] / 1_000_000.0
    else:
        kwh   = data.get("kwh")
        price = data.get("priceperkwh")

    if wallet_key not in knownWallets:
        return False
    if not (isinstance(kwh, (int, float)) and 0 < kwh < 200):
        return False
    if not (isinstance(price, (int, float)) and price > 0):
        return False
    if not verify_signature(wallet_key, data, signature):
        return False
    return True


# ---------------------------------------------------------------------------
# Consensus helper
# ---------------------------------------------------------------------------

def check_consensus(tx_hash: str) -> str:
    """Must be called with state_lock held."""
    vote_list = pending_requests[tx_hash]["votes"]
    approve = list(vote_list.values()).count("APPROVE")
    deny    = list(vote_list.values()).count("BLOCKED")
    total   = len(validatorAddresses)

    if approve > total / 2:
        return "confirmed"
    elif deny > total / 2:
        return "denied"
    return "pending"


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------

def _propagate(data: dict):
    """Broadcast a new transaction to all peer validators."""
    for name, address in validatorAddresses.items():
        if name == "self":
            continue
        try:
            response = requests.post(
                f"http://{address}/propagatedata", json=data, timeout=5
            )
            print(f"Propagated to {name}: HTTP {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"Could not reach {name}: {e}")


def _collect_peer_votes(tx_hash: str):
    """
    Ask every peer how they voted on tx_hash. Called outside state_lock.
    A 200 response means the peer has already settled this tx — we record
    their vote from settled_votes if available, otherwise skip.
    A 404 means they've never seen it — skip.
    """
    for name, address in validatorAddresses.items():
        if name == "self":
            continue
        try:
            response = requests.post(
                f"http://{address}/checkvote",
                json={"hash": tx_hash},
                timeout=5,
            )
            if response.status_code == 205:
                vote = "APPROVE"
            elif response.status_code == 206:
                vote = "BLOCKED"
            else:
                continue  # 200 = settled (we already have their vote), 404 = never seen

            with state_lock:
                if tx_hash in pending_requests:
                    pending_requests[tx_hash]["votes"][address] = vote

        except requests.exceptions.RequestException as e:
            print(f"Could not reach {name} for checkvote: {e}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return "Validator node online", 200


@app.route("/publishdata", methods=["POST"])
def post_data():
    if not request.is_json:
        return "", 400

    data = request.get_json()

    try:
        tx_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        my_vote = "APPROVE" if validate_data(data) else "BLOCKED"

        with state_lock:
            if tx_hash in pending_requests or tx_hash in settled_votes:
                return "", 200
            pending_requests[tx_hash] = {"data": data, "votes": {}}
            pending_requests[tx_hash]["votes"][validatorAddresses["self"]] = my_vote
            settled_votes[tx_hash] = my_vote  # record permanently for /checkvote

        threading.Thread(target=_propagate, args=(data,), daemon=True).start()
        return "", 200

    except Exception as e:
        print(f"/publishdata error: {e}")
        return "", 400


@app.route("/propagatedata", methods=["POST"])
def propagate_data():
    if not request.is_json:
        return "", 400

    data = request.get_json()

    try:
        tx_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        my_vote = "APPROVE" if validate_data(data) else "BLOCKED"

        with state_lock:
            if tx_hash in pending_requests or tx_hash in settled_votes:
                return "", 200
            pending_requests[tx_hash] = {"data": data, "votes": {}}
            pending_requests[tx_hash]["votes"][validatorAddresses["self"]] = my_vote
            settled_votes[tx_hash] = my_vote  # record permanently for /checkvote

        return "", 200

    except Exception as e:
        print(f"/propagatedata error: {e}")
        return "", 400


@app.route("/checkvote", methods=["POST"])
def check_vote():
    """
    Returns this node's vote for a given tx_hash.
    205 = APPROVE, 206 = BLOCKED, 404 = never seen this transaction.
    Answers correctly whether the tx is still pending OR already settled,
    because we record the vote in settled_votes the moment we first see the tx.
    """
    if not request.is_json:
        return "", 400

    data = request.get_json()
    tx_hash = data.get("hash")

    with state_lock:
        vote = settled_votes.get(tx_hash)

    if vote == "APPROVE":
        return "", 205
    elif vote == "BLOCKED":
        return "", 206
    else:
        return "", 404  # genuinely never seen this tx


@app.route("/debug", methods=["GET"])
def debug():
    with state_lock:
        pending_summary = {
            h[:12]: {
                "votes": entry["votes"],
                "walletKey_prefix": entry["data"].get("walletKey", "")[:40],
                "kwh": entry["data"].get("kwh"),
                "priceperkwh": entry["data"].get("priceperkwh"),
            }
            for h, entry in pending_requests.items()
        }
        wallet_fingerprints = {k[:40] + "...": v for k, v in knownWallets.items()}

    return jsonify({
        "self": validatorAddresses["self"],
        "known_wallets": wallet_fingerprints,
        "pending_count": len(pending_summary),
        "pending": pending_summary,
        "confirmed_count": len(transaction_ledger),
        "block_count": len(block_ledger),
        "settled_vote_count": len(settled_votes),
    }), 200


@app.route("/debug/validate", methods=["POST"])
def debug_validate():
    if not request.is_json:
        return "", 400

    data = request.get_json()
    wallet_key = data.get("walletKey", "")
    signature  = data.get("signature", "")

    if "kwh_milli" in data and "price_micro" in data:
        kwh   = data["kwh_milli"]  / 1000.0
        price = data["price_micro"] / 1_000_000.0
        format_used = "arduino_integer_scaled"
    else:
        kwh   = data.get("kwh")
        price = data.get("priceperkwh")
        format_used = "float"

    checks = {
        "format_used":       format_used,
        "wallet_known":      wallet_key.strip().lower() in knownWallets,
        "kwh_in_range":      isinstance(kwh, (int, float)) and 0 < kwh < 200,
        "price_positive":    isinstance(price, (int, float)) and price > 0,
        "signature_valid":   verify_signature(wallet_key, data, signature),
        "wallet_key_prefix": wallet_key[:40] if wallet_key else "(missing)",
    }
    checks["overall"] = all(v for k, v in checks.items() if k not in ("wallet_key_prefix", "format_used"))
    return jsonify(checks), 200


@app.route("/viewledger", methods=["GET"])
def view_ledger():
    try:
        with state_lock:
            snapshot = list(transaction_ledger)
        return jsonify({
            "status": "ok",
            "count": len(snapshot),
            "transactions": snapshot,
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/viewledger/wallets", methods=["GET"])
def view_wallets():
    try:
        with state_lock:
            snapshot = dict(knownWallets)
        return jsonify({"status": "ok", "wallets": snapshot}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/viewledger/blockchain", methods=["GET"])
def view_blockchain():
    try:
        with state_lock:
            blocks = [{"index": i, "hash": h} for i, h in enumerate(block_ledger)]
        return jsonify({
            "status": "ok",
            "blockCount": len(blocks),
            "blocks": blocks,
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Background consensus loop — fully decentralized, no primary
# ---------------------------------------------------------------------------

def consensus_loop():
    while True:
        time.sleep(10)

        with state_lock:
            hashes = list(pending_requests.keys())

        # Collect peer votes outside the lock
        for tx_hash in hashes:
            _collect_peer_votes(tx_hash)

        # Tally and commit independently — outcome is deterministic because
        # both nodes apply the same validation logic to the same data and
        # share votes via /checkvote, so they always reach the same conclusion.
        with state_lock:
            for tx_hash in list(pending_requests.keys()):
                status = check_consensus(tx_hash)

                if status == "confirmed":
                    entry = pending_requests.pop(tx_hash)
                    transaction_ledger.append(entry)
                    wallet_key = entry["data"].get("walletKey", "").strip().lower()
                    if wallet_key in knownWallets:
                        knownWallets[wallet_key] += 1
                    print(f"Confirmed: {tx_hash[:12]}...")

                elif status == "denied":
                    pending_requests.pop(tx_hash)
                    print(f"Denied:    {tx_hash[:12]}...")

            committed   = len(block_ledger) * BLOCK_SIZE
            uncommitted = len(transaction_ledger) - committed
            if uncommitted >= BLOCK_SIZE:
                batch = transaction_ledger[committed: committed + BLOCK_SIZE]
                block_hash = hashlib.sha256(
                    json.dumps(batch, sort_keys=True).encode()
                ).hexdigest()
                block_ledger.append(block_hash)
                print(f"Block {len(block_ledger)} committed: {block_hash}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    HOST = "0.0.0.0"
    PORT = 4020
    threading.Thread(target=consensus_loop, daemon=True).start()
    app.run(host=HOST, port=PORT, debug=True, use_reloader=False)