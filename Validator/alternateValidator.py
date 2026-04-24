import time
import json
import hashlib
import threading

import requests
import rsa
from flask import Flask, request, jsonify

app = Flask(__name__)

# Storage for wallet public keys (PEM strings) -> coin balance
knownWallets = {
    "-----BEGIN RSA PUBLIC KEY-----MEgCQQCpl8DS/uo1YF4eOReLAKCgC...": 0,
    "[walletB PEM PUBLIC KEY HERE]": 0,
}

# Storage for validator IP addresses
validatorAddresses = {
    "self": "10.0.0.45",
    "validatorA": "10.0.0.231",
}

# Temp storage for pending consensus votes  { hash -> {data, votes} }
pending_requests = {}

# Approved transactions
transaction_ledger = []

# Hashes of committed blocks
block_ledger = []

# How many transactions to bundle into a block
BLOCK_SIZE = 3


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def load_public_key(pem_string: str) -> rsa.PublicKey:
    """Load an RSA public key from a PEM string."""
    return rsa.PublicKey.load_pkcs1(pem_string.encode())


def verify_signature(public_key_pem: str, message: dict, signature_hex: str) -> bool:
    """
    Verify that `signature_hex` is a valid RSA-SHA256 signature over the
    canonical JSON representation of `message` (signature field excluded).
    Returns True if valid, False otherwise.
    """
    try:
        payload = {k: v for k, v in message.items() if k != "signature"}
        message_bytes = json.dumps(payload, sort_keys=True).encode()
        signature_bytes = bytes.fromhex(signature_hex)
        pub_key = load_public_key(public_key_pem)
        rsa.verify(message_bytes, signature_bytes, pub_key)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_data(data: dict) -> bool:
    wallet_key = data.get("walletKey")
    kwh = data.get("kwh")
    price_per_kwh = data.get("priceperkwh")
    signature = data.get("signature")

    # Must be a known wallet
    if wallet_key not in knownWallets:
        return False

    # FIX: original condition was inverted — `0 < kwh < 200` returned False
    # for valid readings, blocking everything.  We want to REJECT out-of-range.
    if not (0 < kwh < 200):
        return False

    if price_per_kwh <= 0:
        return False

    # Verify digital signature
    if not verify_signature(wallet_key, data, signature):
        return False

    return True


# ---------------------------------------------------------------------------
# Consensus helpers
# ---------------------------------------------------------------------------

def check_consensus(tx_hash: str) -> str:
    """
    Returns 'confirmed', 'denied', or 'pending' based on current votes.
    A simple majority of *all* known validators is required.
    """
    vote_list = pending_requests[tx_hash]["votes"]
    approve = list(vote_list.values()).count("APPROVE")
    deny = list(vote_list.values()).count("BLOCKED")
    total = len(validatorAddresses)

    if approve > total / 2:
        return "confirmed"
    elif deny > total / 2:
        return "denied"
    return "pending"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return "Validator node online", 200


@app.route("/publishdata", methods=["POST"])
def post_data():
    """
    Entry point for sensor data.  The receiving validator:
      1. Stores the data as a pending request.
      2. Casts its own vote.
      3. Propagates the data to every OTHER validator.
    """
    if not request.is_json:
        return "", 400

    data = request.get_json()

    try:
        tx_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

        if tx_hash in pending_requests:
            # Already seen this transaction — ignore duplicate
            return "", 200

        pending_requests[tx_hash] = {"data": data, "votes": {}}
        my_vote = "APPROVE" if validate_data(data) else "BLOCKED"
        pending_requests[tx_hash]["votes"][validatorAddresses["self"]] = my_vote

        # Propagate to every validator except ourselves
        for name, address in validatorAddresses.items():
            if name == "self":
                continue
            url = f"http://{address}/propagatedata"
            try:
                response = requests.post(url, json=data, timeout=5)
                print(f"Propagated to {name}: HTTP {response.status_code}")
            except requests.exceptions.RequestException as e:
                print(f"Could not reach {name}: {e}")

        return "", 200

    except Exception as e:
        print(f"/publishdata error: {e}")
        return "", 400


@app.route("/propagatedata", methods=["POST"])
def propagate_data():
    """
    Called by another validator to share a transaction.  This node validates
    and records its own vote but does NOT re-broadcast.
    """
    if not request.is_json:
        return "", 400

    data = request.get_json()

    try:
        tx_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

        if tx_hash in pending_requests:
            return "", 200  # Already have it

        pending_requests[tx_hash] = {"data": data, "votes": {}}
        my_vote = "APPROVE" if validate_data(data) else "BLOCKED"
        pending_requests[tx_hash]["votes"][validatorAddresses["self"]] = my_vote

        return "", 200

    except Exception as e:
        print(f"/propagatedata error: {e}")
        return "", 400


@app.route("/checkvote", methods=["POST"])
def check_vote():
    """
    Let another validator query how this node voted on a given hash.
    Returns 205 = APPROVE, 206 = BLOCKED, 404 = unknown hash.
    """
    if not request.is_json:
        return "", 400

    data = request.get_json()
    tx_hash = data.get("hash")

    try:
        my_vote = pending_requests[tx_hash]["votes"][validatorAddresses["self"]]
        return ("", 205) if my_vote == "APPROVE" else ("", 206)
    except KeyError:
        return "", 404


@app.route("/viewledger", methods=["GET"])
def view_ledger():
    try:
        return jsonify({
            "status": "ok",
            "count": len(transaction_ledger),
            "transactions": transaction_ledger,
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/viewledger/wallets", methods=["GET"])
def view_wallets():
    try:
        return jsonify({"status": "ok", "wallets": knownWallets}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/viewledger/blockchain", methods=["GET"])
def view_blockchain():
    try:
        blocks = [{"index": i, "hash": h} for i, h in enumerate(block_ledger)]
        return jsonify({
            "status": "ok",
            "blockCount": len(block_ledger),
            "blocks": blocks,
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Background consensus loop
# ---------------------------------------------------------------------------

def consensus_loop():
    while True:
        time.sleep(10)

        for tx_hash in list(pending_requests.keys()):
            status = check_consensus(tx_hash)

            if status == "confirmed":
                entry = pending_requests.pop(tx_hash)
                transaction_ledger.append(entry)
                wallet_key = entry["data"].get("walletKey")
                if wallet_key in knownWallets:
                    knownWallets[wallet_key] += 1

            elif status == "denied":
                pending_requests.pop(tx_hash)

        # Commit a new block whenever we have accumulated another BLOCK_SIZE
        # transactions since the last block.
        committed = len(block_ledger) * BLOCK_SIZE
        uncommitted = len(transaction_ledger) - committed
        if uncommitted >= BLOCK_SIZE:
            batch = transaction_ledger[committed: committed + BLOCK_SIZE]
            # FIX: use json.dumps for a stable, deterministic representation
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
    app.run(host=HOST, port=PORT, debug=True)