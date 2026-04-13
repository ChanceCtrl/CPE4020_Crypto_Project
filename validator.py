from flask import Flask, request, jsonify
import hashlib
import requests

app = Flask(__name__)

# Storage for wallet/sensor public keys
knownWallets = {
    "walletA": "[KEY HERE]",
    "walletB": "[KEY HERE]"
}

# Storage for validator IP addresses
validatorAddresses = {
    "self": "[IP HERE]",
    "validatorA": "[IP HERE]",
    "validatorB": "[IP HERE]"
}

# This is the format we expect from the sensor
exampledata = {
    "walletKey": "[PUBLIC KEY]",
    "timeStamp": 0,
    "kwh": 75,
    "priceperkwh": 0.2,
    "signature": "[DIGITAL SIGNATURE USING PRIVATE KEY]"
}

# Voting and Consensus Stuff
pending_requests = {}


# Used while we are tallying votes
def check_consensus(hash):
    vote_list = pending_requests[hash]

    approve = list(vote_list.values()).count("approve")
    total = len(vote_list)

    if approve > total / 2:
        return "confirmed"

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
        pending_requests[hashlib.sha256(data.encode("utf-8"))] = {}

        if validate_data(data):
            pending_requests[hashlib.sha256(data.encode("utf-8"))][validatorAddresses["self"]] = "APPROVE"
        else:
            pending_requests[hashlib.sha256(data.encode("utf-8"))][validatorAddresses["self"]] = "BLOCKED"

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
        pending_requests[hashlib.sha256(data.encode("utf-8"))] = {}

        if validate_data(data):
            pending_requests[hashlib.sha256(data.encode("utf-8"))][validatorAddresses["self"]] = "APPROVE"
        else:
            pending_requests[hashlib.sha256(data.encode("utf-8"))][validatorAddresses["self"]] = "BLOCKED"

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
            return "", 205
        else:
            return "", 206

    except:
        return "", 404


@app.route("/viewledger")
def show_report():
    try:
        return jsonify({"messages": comments}), 200
    except:
        return "", 404


if __name__ == "__main__":
    HOST = "0.0.0.0"  # Use '0.0.0.0' to make the server accessible externally
    PORT = 4020  # Set your desired port number
    app.run(host=HOST, port=PORT, debug=True)
