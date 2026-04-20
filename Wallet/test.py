# import hashlib
# from hashlib import sha256
#
# import rsa
#
# # Private key decryption
# def fun1():
#     publicKey, privateKey = rsa.newkeys(512)
#
#     text = "Mrow :3"
#     fella = rsa.sign_hash(sha256(text.encode()).digest(), privateKey, "SHA-256")
#
#     print({text, fella})
#
#     text2 = "Mrow2 3 :3"
#     print(rsa.verify(text.encode(), fella, publicKey))
#     print(rsa.verify(text2.encode(), fella, publicKey))
#
#     # print(text.decode())
#
#
# fun1()  # success

from hashlib import sha256
from json import dumps

import rsa
from rsa import PublicKey

# Generate keys
publicKey, privateKey = rsa.newkeys(512)

print(f"Pub:\n{publicKey.save_pkcs1().decode()}")

plain_pub = publicKey.save_pkcs1().decode()

exampledata_2 = {
    "walletKey": plain_pub,
    "timeStamp": 0,
    "kwh": 75,
    "priceperkwh": 0.2,
}

exampledata = {
    **exampledata_2,
    "signature": signature,
}

# IMPORTANT: stable JSON (order matters)
message = dumps(exampledata_2, sort_keys=True).encode()

# SIGN MESSAGE DIRECTLY (not hash manually)
signature = rsa.sign(message, privateKey, "SHA-256")


def auth_wallet(data: dict, signature: bytes) -> bool:
    try:
        public_key = PublicKey.load_pkcs1(data["walletKey"].encode())

        message = dumps(
            {k: data[k] for k in data if k != "signature"}, sort_keys=True
        ).encode()

        rsa.verify(message, signature, public_key)

        return True

    except Exception as e:
        print(f"Error verifying: {e}")
        return False


print(auth_wallet(exampledata, exampledata["signature"]))
