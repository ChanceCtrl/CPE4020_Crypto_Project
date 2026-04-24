import time
import json
import hashlib
import threading

import requests
import rsa
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # allow dashboard HTML (different origin) to fetch from this server

# ---------------------------------------------------------------------------
# Shared state + lock
# ---------------------------------------------------------------------------

state_lock = threading.Lock()

def _load_known_wallets(names):
    wallets = {}
    for name in names:
        path = f"{name}_public.pem"
        try:
            pem = open(path, "rb").read().decode().replace("\r\n", "\n").strip()
            wallets[pem] = 0
            print(f"Loaded public key: {path}")
        except FileNotFoundError:
            print(f"WARNING: {path} not found — run keygen.py first")
    return wallets

knownWallets = _load_known_wallets(["walletA", "walletB"])

validatorAddresses = {
    "self":       "[MY IP HERE]:4020",
    "validatorA": "[VALIDATOR A IP HERE]:4020",
}

pending_requests = {}
transaction_ledger = []
block_ledger = []

# Hashes we have already confirmed or denied — used to suppress stale
# /checkvote queries from peers that haven't caught up yet.
settled_hashes = set()

BLOCK_SIZE = 3


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def load_public_key(pem_str: str) -> rsa.PublicKey:
    return rsa.PublicKey.load_pkcs1(pem_str.encode())


def verify_signature(public_key_pem: str, message: dict, signature_hex: str) -> bool:
    try:
        public_key_pem = public_key_pem.replace("\r\n", "\n").strip()
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
    wallet_key = data.get("walletKey", "").replace("\r\n", "\n").strip()
    kwh        = data.get("kwh")
    price      = data.get("priceperkwh")
    signature  = data.get("signature")

    if wallet_key not in knownWallets:
        return False
    if not (0 < kwh < 200):
        return False
    if price <= 0:
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
    Ask every peer how they voted on tx_hash. Called outside state_lock
    since it does network I/O. Silently skips 404s — the peer either
    hasn't received the tx yet or has already settled it.
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
                # 404 means the peer has already settled or never saw it — stop asking
                return

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
            if tx_hash in pending_requests or tx_hash in settled_hashes:
                return "", 200
            pending_requests[tx_hash] = {"data": data, "votes": {}}
            pending_requests[tx_hash]["votes"][validatorAddresses["self"]] = my_vote

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
            if tx_hash in pending_requests or tx_hash in settled_hashes:
                return "", 200
            pending_requests[tx_hash] = {"data": data, "votes": {}}
            pending_requests[tx_hash]["votes"][validatorAddresses["self"]] = my_vote

        return "", 200

    except Exception as e:
        print(f"/propagatedata error: {e}")
        return "", 400


@app.route("/checkvote", methods=["POST"])
def check_vote():
    """
    Returns 205 = APPROVE, 206 = BLOCKED.
    Returns 404 only if this hash is completely unknown (never seen it).
    Returns 200 if already settled — tells the peer to stop asking.
    """
    if not request.is_json:
        return "", 400

    data = request.get_json()
    tx_hash = data.get("hash")

    with state_lock:
        if tx_hash in settled_hashes:
            return "", 200  # already settled — peer can stop querying
        try:
            my_vote = pending_requests[tx_hash]["votes"][validatorAddresses["self"]]
            return ("", 205) if my_vote == "APPROVE" else ("", 206)
        except KeyError:
            return "", 404


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
        "settled_count": len(settled_hashes),
    }), 200


@app.route("/debug/validate", methods=["POST"])
def debug_validate():
    if not request.is_json:
        return "", 400

    data = request.get_json()
    wallet_key = data.get("walletKey", "")
    kwh        = data.get("kwh")
    price      = data.get("priceperkwh")
    signature  = data.get("signature", "")

    checks = {
        "wallet_known":    wallet_key.replace("\r\n", "\n").strip() in knownWallets,
        "kwh_in_range":    isinstance(kwh, (int, float)) and 0 < kwh < 200,
        "price_positive":  isinstance(price, (int, float)) and price > 0,
        "signature_valid": verify_signature(wallet_key, data, signature),
        "wallet_key_prefix": wallet_key[:40] if wallet_key else "(missing)",
    }
    checks["overall"] = all(v for k, v in checks.items() if k != "wallet_key_prefix")
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
# Background consensus loop
# ---------------------------------------------------------------------------

def consensus_loop():
    while True:
        time.sleep(10)

        # Snapshot pending hashes outside the lock before doing network I/O
        with state_lock:
            hashes = list(pending_requests.keys())

        for tx_hash in hashes:
            _collect_peer_votes(tx_hash)

        with state_lock:
            for tx_hash in list(pending_requests.keys()):
                status = check_consensus(tx_hash)

                if status == "confirmed":
                    entry = pending_requests.pop(tx_hash)
                    settled_hashes.add(tx_hash)
                    transaction_ledger.append(entry)
                    wallet_key = entry["data"].get("walletKey", "").replace("\r\n", "\n").strip()
                    if wallet_key in knownWallets:
                        knownWallets[wallet_key] += 1
                    print(f"Confirmed: {tx_hash[:12]}...")

                elif status == "denied":
                    pending_requests.pop(tx_hash)
                    settled_hashes.add(tx_hash)
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