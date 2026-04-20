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

    def auth_wallet(self, data: dict[str, str | int | float], signature: str) -> bool:
        public_key = data.get("walletKey")

        try:
            rsa.verify(dumps(data), signature.encode(), PublicKey(public_key))
        except Exception as E:
            print(f"Error verifing: {E}")
            return False
        return True


# fella = evWalletChain()
#
# fella.register_new_wallet()
