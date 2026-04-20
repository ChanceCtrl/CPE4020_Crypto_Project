from base64 import b64decode
from json import dumps

import rsa
from rsa import PublicKey


class evWalletChain:
    known_wallets: list[str]

    def register_new_wallet(
        self, data: dict[str, str | int | float], signature: str
    ) -> bool:
        # Check if they own that public_key
        self.auth_wallet(data, signature)

        public_key = str(data.get("walletKey"))

        # Check if any wallets already have that public_key stored
        if public_key in self.known_wallets:
            return False

        self.known_wallets.append(public_key)

        return True


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
