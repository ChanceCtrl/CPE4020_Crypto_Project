import rsa


class evWalletChain:
    known_wallets: list[str]

    def register_new_wallet(self, public_key: str, signature: str) -> bool:

        # Check if any wallets already have that public_key stored
        if public_key in self.known_wallets:
            return False

        self.known_wallets.append(public_key)

        return True

    def auth_wallet(self, data, signature: str) -> bool:
        return True
