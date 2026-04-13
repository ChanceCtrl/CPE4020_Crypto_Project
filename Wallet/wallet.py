import time
import requests

NODE_URL = "http://localhost:5000"

WALLET_A = "wallet_A"
WALLET_B = "wallet_B"


def send_energy(kwh):
    tx = {
        "device_id": "EV_001",
        "timestamp": int(time.time()),
        "kwh": kwh,
        "wallet": WALLET_A,
        "signature": "PLAINTEXT_SIGNATURE",
    }

    r = requests.post(f"{NODE_URL}/submit_energy", json=tx)
    print(r.json())


def send_transfer(amount):
    tx = {
        "from": WALLET_A,
        "to": WALLET_B,
        "amount": amount,
        "timestamp": int(time.time()),
        "signature": "PLAINTEXT_SIGNATURE",
    }

    r = requests.post(f"{NODE_URL}/transfer", json=tx)
    print(r.json())


if __name__ == "__main__":
    send_energy(10)
    send_transfer(5)
