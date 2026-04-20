from flask import Flask, jsonify, render_template_string
import threading
import time
import random

app = Flask(__name__)

# -----------------------
# Simulated Data
# -----------------------
validators = [{"id": i, "stake": random.randint(50, 200), "status": "active"} for i in range(5)]
wallets = {f"0x{i:04X}": random.randint(100, 1000) for i in range(10)}
blocks = []
block_height = 0

# -----------------------
# Background Simulation
# -----------------------
def simulate_blockchain():
    global block_height

    while True:
        time.sleep(2)

        block_height += 1
        validator = random.choice(validators)

        tx_count = random.randint(1, 5)
        transactions = []

        for _ in range(tx_count):
            sender = random.choice(list(wallets.keys()))
            receiver = random.choice(list(wallets.keys()))
            amount = random.randint(1, 20)

            if wallets[sender] >= amount:
                wallets[sender] -= amount
                wallets[receiver] += amount

                transactions.append({
                    "from": sender,
                    "to": receiver,
                    "amount": amount
                })

        block = {
            "height": block_height,
            "validator": validator["id"],
            "tx_count": len(transactions),
            "transactions": transactions,
            "time": time.strftime("%H:%M:%S")
        }

        blocks.insert(0, block)

        if len(blocks) > 50:   # allow more history now
            blocks.pop()


threading.Thread(target=simulate_blockchain, daemon=True).start()

# -----------------------
# API
# -----------------------
@app.route("/api/data")
def get_data():
    return jsonify({
        "block_height": block_height,
        "validators": validators,
        "wallets": wallets,
        "blocks": blocks
    })

# -----------------------
# UI
# -----------------------
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>DePIN Dashboard (Simulation)</title>

    <style>
        body {
            font-family: Arial;
            background: #0f172a;
            color: white;
            margin: 0;
            height: 100vh;
            overflow: hidden;
        }

        h1 {
            text-align: center;
            margin: 10px 0;
        }

        .container {
            display: flex;
            gap: 20px;
            padding: 20px;
            height: calc(100vh - 60px);
            box-sizing: border-box;
        }

        .panel {
            background: #1e293b;
            padding: 15px;
            border-radius: 10px;
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        .panel h2 {
            margin: 0 0 10px 0;
        }

        /* NETWORK + WALLET SCROLL (simple) */
        .scroll-box {
            flex: 1;
            overflow-y: auto;
            border-top: 1px solid #334155;
            padding-top: 10px;
        }

        /* BLOCKS: dedicated strong scroll region */
        .blocks-scroll {
            flex: 1;
            overflow-y: auto;
            border-top: 1px solid #334155;
            padding-top: 10px;
            scroll-behavior: smooth;
        }

        .block {
            border-bottom: 1px solid #334155;
            padding: 8px 5px;
        }

        .block b {
            color: #60a5fa;
        }
    </style>
</head>

<body>

<h1>DePIN Dashboard (Simulated)</h1>

<div class="container">

    <div class="panel">
        <h2>Network</h2>
        <div class="scroll-box" id="network"></div>
    </div>

    <div class="panel">
        <h2>Blocks</h2>
        <div class="blocks-scroll" id="blocks"></div>
    </div>

    <div class="panel">
        <h2>Wallets</h2>
        <div class="scroll-box" id="wallets"></div>
    </div>

</div>

<script>
async function fetchData() {
    const res = await fetch('/api/data');
    const data = await res.json();

    // Network
    document.getElementById("network").innerHTML =
        "Block Height: " + data.block_height + "<br>" +
        "Validators: " + data.validators.length;

    // Blocks
    let blocksHTML = "";
    data.blocks.forEach(b => {
        blocksHTML += `
            <div class="block">
                <b>Block ${b.height}</b> | Validator ${b.validator} <br>
                TXs: ${b.tx_count} | Time: ${b.time}
            </div>
        `;
    });

    const blockDiv = document.getElementById("blocks");
    blockDiv.innerHTML = blocksHTML;

    // Auto-scroll to newest block (top since we insert newest first)
    blockDiv.scrollTop = 0;

    // Wallets
    let walletsHTML = "";
    for (const [addr, bal] of Object.entries(data.wallets)) {
        walletsHTML += `${addr}: ${bal} coins<br>`;
    }
    document.getElementById("wallets").innerHTML = walletsHTML;
}

setInterval(fetchData, 2000);
fetchData();
</script>

</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)

# -----------------------
# RUN
# -----------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
