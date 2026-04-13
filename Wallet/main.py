import hashlib
import json
import sys
import threading
import time
from collections import Counter, defaultdict

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# -----------------------------
# CONFIG
# -----------------------------
NODE_ID = int(sys.argv[1])
PORT = 5000 + NODE_ID
BOOTSTRAP = "http://localhost:4000"

# -----------------------------
# STATE
# -----------------------------
PEERS = set()
ledger = {}
mempool = []
blockchain = []

prepared = defaultdict(set)
committed = defaultdict(set)
block_store = {}

view = 0
lock = threading.Lock()


# -----------------------------
# HELPERS
# -----------------------------
def f():
    return (len(PEERS) + 1 - 1) // 3


def quorum():
    return 2 * f() + 1


def is_primary():
    return NODE_ID == view % (len(PEERS) + 1)


def hash_block(block):
    return hashlib.sha256(json.dumps(block, sort_keys=True).encode()).hexdigest()


def hash_chain(chain):
    return hashlib.sha256(json.dumps(chain, sort_keys=True).encode()).hexdigest()


def broadcast(path, data):
    for peer in PEERS:
        try:
            requests.post(f"http://{peer}{path}", json=data, timeout=1)
        except:
            pass


# -----------------------------
# BOOTSTRAP
# -----------------------------
def register_with_bootstrap():
    global PEERS

    res = requests.post(f"{BOOTSTRAP}/register", json={"address": f"localhost:{PORT}"})

    peers = res.json()["peers"]
    PEERS = set(peers)
    PEERS.discard(f"localhost:{PORT}")

    print(f"[Node {NODE_ID}] Peers: {PEERS}")


def refresh_peers():
    global PEERS

    while True:
        try:
            res = requests.get(f"{BOOTSTRAP}/peers")
            peers = set(res.json())
            peers.discard(f"localhost:{PORT}")
            PEERS = peers
        except:
            pass

        time.sleep(5)


# -----------------------------
# STATE SYNC (PBFT STYLE)
# -----------------------------
def sync_state_pbft():
    global blockchain, ledger

    print(f"[Node {NODE_ID}] Starting PBFT state sync...")

    responses = []

    for peer in PEERS:
        try:
            res = requests.get(f"http://{peer}/state", timeout=2)
            data = res.json()

            chain = data["chain"]
            chain_hash = hash_chain(chain)

            responses.append((chain_hash, chain))
        except:
            continue

    if not responses:
        print("No peers responded")
        return

    # Count matching chains
    counts = Counter([h for h, _ in responses])

    for chain_hash, count in counts.items():
        if count >= quorum():
            print(f"[Node {NODE_ID}] State agreed by quorum ({count})")

            # Get matching chain
            for h, chain in responses:
                if h == chain_hash:
                    blockchain = chain
                    rebuild_ledger()
                    return

    print(f"[Node {NODE_ID}] No quorum reached for state sync")


def rebuild_ledger():
    global ledger
    ledger = {}

    for block in blockchain:
        for tx in block["transactions"]:
            if "kwh" in tx:
                ledger[tx["wallet"]] = ledger.get(tx["wallet"], 0) + tx["kwh"]
            elif "amount" in tx:
                if ledger.get(tx["from"], 0) >= tx["amount"]:
                    ledger[tx["from"]] -= tx["amount"]
                    ledger[tx["to"]] = ledger.get(tx["to"], 0) + tx["amount"]


# -----------------------------
# TX LOGIC
# -----------------------------
def apply_energy(tx):
    ledger[tx["wallet"]] = ledger.get(tx["wallet"], 0) + tx["kwh"]


def apply_transfer(tx):
    if ledger.get(tx["from"], 0) >= tx["amount"]:
        ledger[tx["from"]] -= tx["amount"]
        ledger[tx["to"]] = ledger.get(tx["to"], 0) + tx["amount"]


# -----------------------------
# BLOCK CREATION
# -----------------------------
def create_block():
    return {
        "index": len(blockchain),
        "timestamp": time.time(),
        "transactions": mempool.copy(),
        "previous_hash": blockchain[-1]["hash"] if blockchain else "0",
    }


# -----------------------------
# PBFT CONSENSUS
# -----------------------------
def start_consensus():
    if not is_primary():
        print(f"[Node {NODE_ID}] Not leader")
        return

    block = create_block()
    h = hash_block(block)
    block["hash"] = h
    block_store[h] = block

    msg = {"type": "PRE-PREPARE", "block": block, "sender": NODE_ID}

    broadcast("/pbft", msg)
    handle_preprepare(msg)


def handle_preprepare(msg):
    block = msg["block"]
    h = block["hash"]

    block_store[h] = block

    msg = {"type": "PREPARE", "block_hash": h, "sender": NODE_ID}
    broadcast("/pbft", msg)
    handle_prepare(msg)


def handle_prepare(msg):
    h = msg["block_hash"]
    prepared[h].add(msg["sender"])

    if len(prepared[h]) >= quorum():
        msg = {"type": "COMMIT", "block_hash": h, "sender": NODE_ID}
        broadcast("/pbft", msg)
        handle_commit(msg)


def handle_commit(msg):
    h = msg["block_hash"]
    committed[h].add(msg["sender"])

    if len(committed[h]) >= quorum():
        finalize_block(h)


def finalize_block(h):
    if h not in block_store:
        return

    with lock:
        if any(b["hash"] == h for b in blockchain):
            return

        block = block_store[h]

        for tx in block["transactions"]:
            if "kwh" in tx:
                apply_energy(tx)
            elif "amount" in tx:
                apply_transfer(tx)

        blockchain.append(block)
        mempool.clear()

        print(f"[Node {NODE_ID}] Block committed: {h}")


# -----------------------------
# ROUTES
# -----------------------------
@app.route("/submit_energy", methods=["POST"])
def submit_energy():
    mempool.append(request.json)
    return jsonify({"status": "queued"})


@app.route("/transfer", methods=["POST"])
def transfer():
    mempool.append(request.json)
    return jsonify({"status": "queued"})


@app.route("/pbft", methods=["POST"])
def pbft():
    msg = request.json

    if msg["type"] == "PRE-PREPARE":
        handle_preprepare(msg)
    elif msg["type"] == "PREPARE":
        handle_prepare(msg)
    elif msg["type"] == "COMMIT":
        handle_commit(msg)

    return jsonify({"status": "ok"})


@app.route("/start", methods=["GET"])
def start():
    threading.Thread(target=start_consensus).start()
    return jsonify({"status": "started"})


@app.route("/state", methods=["GET"])
def state():
    return jsonify({"chain": blockchain})


@app.route("/ledger")
def ledger_view():
    return jsonify(ledger)


# -----------------------------
# STARTUP
# -----------------------------
if __name__ == "__main__":
    register_with_bootstrap()

    time.sleep(2)  # allow peers to register

    sync_state_pbft()  # 🔥 PBFT SAFE SYNC

    threading.Thread(target=refresh_peers, daemon=True).start()

    print(f"Node {NODE_ID} running on {PORT}")
    app.run(port=PORT)
