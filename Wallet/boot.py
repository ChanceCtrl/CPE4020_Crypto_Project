from flask import Flask, request, jsonify

app = Flask(__name__)

validators = set()


@app.route("/register", methods=["POST"])
def register():
    data = request.json
    address = data["address"]

    validators.add(address)

    return jsonify({"peers": list(validators)})


@app.route("/peers", methods=["GET"])
def get_peers():
    return jsonify(list(validators))


if __name__ == "__main__":
    app.run(port=4000)
