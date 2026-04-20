import hashlib
from hashlib import sha256

import rsa


# Private key decryption
def fun1():
    publicKey, privateKey = rsa.newkeys(512)

    text = "Mrow :3"
    fella = rsa.sign_hash(sha256(text.encode()).digest(), privateKey, "SHA-256")

    print({text, fella})

    text2 = "Mrow2 3 :3"
    print(rsa.verify(text.encode(), fella, publicKey))
    print(rsa.verify(text2.encode(), fella, publicKey))

    # print(text.decode())


fun1()  # success
