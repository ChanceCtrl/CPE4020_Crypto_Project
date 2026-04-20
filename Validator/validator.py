import time

from flask import Flask, request, jsonify
import hashlib
import requests
import rsa

app = Flask(__name__)

# Storage for wallet/sensor public keys
knownWallets = {
    "[walletA KEY HERE]": 0, #balance
    "[walletB KEY HERE]": 0, #balance
}

# Storage for validator IP addresses
validatorAddresses = {
    "self": "[IP HERE]",
    "validatorA": "[IP HERE]"
}

# This is the format we expect from the sensor
exampledata = {
    "walletKey": "[PUBLIC KEY]",
    "timeStamp": 0,
    "kwh": 75,
    "priceperkwh": 0.2,
    "signature": "[DIGITAL SIGNATURE USING PRIVATE KEY]"
}

#this is the format we will save transactions as
exampleTransaction = {
    "walletKey": "[PUBLIC KEY]",
    "timeStamp": 0,
    "kwh": 75,
    "priceperkwh": 0.2,
    "signature": "[DIGITAL SIGNATURE USING PRIVATE KEY]"
}

# temp storage for voting and Consensus Stuff
pending_requests = {}

#Once approved, new transactions are pushed to entire ledger
transaction_ledger = []

#every three transactions get hashed into a block and added here
block_ledger = []
#how many transactions to bundle into a block
blockSize = 3

# Used while we are tallying votes
def check_consensus(hash):
    vote_list = pending_requests[hash].votes

    approve = list(vote_list.values()).count("APPROVE")
    deny = list(vote_list.values()).count("DENY")
    total = len(vote_list)

    if approve > total / 2:
        return "confirmed"
    elif deny > total / 2:
            return "denied"
    return "pending"


def validate_data(data):
    sensorName = data.get("sensorName")
    kwh = data.get("kwh")
    priceperkwh = data.get("priceperkwh")
    signature = data.get("signature")

    if sensorName not in knownWallets:
        return False

    if 0 > kwh > 200:
        return False

    if priceperkwh <= 0:
        return False

def verify_signature(signature):
    #use the rsa_verify thingy
    return False

@app.route("/")
def home():
    try:
        return "Hello, World!", 200
    except:
        return "", 404


@app.route("/publishdata", methods=["POST"])
def post_data():
    if not request.is_json:
        return "", 400

    data = request.get_json()
    sensorName = data.get("sensorName")
    kwh = data.get("kwh")
    priceperkwh = data.get("priceperkwh")
    signature = data.get("signature")

    # Store the message in the pending_push array and validate
    try:
        pending_requests[hashlib.sha256(data.encode("utf-8")).hexdigest()] = {}
        pending_requests[hashlib.sha256(data.encode("utf-8")).hexdigest()]["data"] = data
        pending_requests[hashlib.sha256(data.encode("utf-8")).hexdigest()]["votes"] = {}

        if validate_data(data):
            pending_requests[hashlib.sha256(data.encode("utf-8")).hexdigest()]["votes"][validatorAddresses["self"]] = "APPROVE"
        else:
            pending_requests[hashlib.sha256(data.encode("utf-8")).hexdigest()]["votes"][validatorAddresses["self"]] = "BLOCKED"

        # since this endpoint is used to deceminate the data, we need to broadcast to all other validators
        for i, j in validatorAddresses:
            url = "http://" + j + "/propagatedata"
            try:
                # Send POST request
                response = requests.post(url, json=data)

                # Print response details
                print("Status Code:", response.status_code)
            except requests.exceptions.RequestException as e:
                print("An error occurred:", e)

        return "", 200
    except:
        return "", 400


@app.route("/propagatedata", methods=["POST"])
def propagatedata():
    if not request.is_json:
        return "", 400

    data = request.get_json()

    # Store the message in the pending_push array and validate
    try:
        pending_requests[hashlib.sha256(data.encode("utf-8")).hexdigest()] = {}
        pending_requests[hashlib.sha256(data.encode("utf-8")).hexdigest()]["data"] = data
        pending_requests[hashlib.sha256(data.encode("utf-8")).hexdigest()]["votes"] = {}

        if validate_data(data):
            pending_requests[hashlib.sha256(data.encode("utf-8"))].hexdigest()["votes"][validatorAddresses["self"]] = "APPROVE"
        else:
            pending_requests[hashlib.sha256(data.encode("utf-8"))].hexdigest()["votes"][validatorAddresses["self"]] = "BLOCKED"

        return "", 200
    except:
        return "", 400


@app.route("/checkvote", methods=["POST"])
def request_vote():
    if not request.is_json:
        return "", 400

    data = request.get_json()
    hash = data.get("hash")

    try:
        if pending_requests[hash][validatorAddresses["self"]] == "APPROVE":
            return "", 205 #205 means I APPROVE
        else:
            return "", 206 #206 means I DENY

    except:
        return "", 404 #something bad happened


@app.route("/viewledger")
def show_report():
    try:
        return jsonify(transaction_ledger), 200
    except:
        return "", 404

@app.route("/viewledger/wallets")
def show_report():
    try:
        return jsonify(knownWallets), 200
    except:
        return "", 404

@app.route("/viewledger/blockchain")
def show_report():
    try:
        return jsonify(block_ledger), 200
    except:
        return "", 404

if __name__ == "__main__":
    HOST = "0.0.0.0"  # Use '0.0.0.0' to make the server accessible externally
    PORT = 4020  # Set your desired port number
    app.run(host=HOST, port=PORT, debug=True)

while True:
    # check every 10 seconds the vote status for pending requests
    time.sleep(10)

    for i in pending_requests.keys():
        status = check_consensus(i)
        if status == "confirmed":
            transaction_ledger[i] = pending_requests[i] #add pending request to the ledger
            knownWallets[pending_requests[i]["data"].walletKey] += 1 #give a coin!
            pending_requests.pop(i) #accepted to remove
        elif status == "denied":
            pending_requests.pop(i) #denied so remove

    # add new transactions to blockchain
    if (len(transaction_ledger)%blockSize == 0) and (len(block_ledger) > 0) and (len(transaction_ledger)/blockSize > (len(block_ledger))):
        # add every three new transactions to our blockchain
        block_ledger.append(hashlib.sha256("|".join(block_ledger).encode('utf-8')).hexdigest())
