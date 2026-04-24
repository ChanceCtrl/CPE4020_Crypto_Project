"""
DePIN Keygen — run this once before starting your nodes.

Generates RSA keypairs for walletA and walletB and saves them to .pem files.
The validator and test client will load keys from these files at startup.

Usage:
    python keygen.py

Output files:
    walletA_public.pem   walletA_private.pem
    walletB_public.pem   walletB_private.pem
"""

import rsa
from pathlib import Path

WALLETS = ["walletA", "walletB"]
KEY_SIZE = 512  # fine for a PoC; use 2048+ in production

for name in WALLETS:
    print(f"Generating {KEY_SIZE}-bit keypair for {name}...")
    pub, priv = rsa.newkeys(KEY_SIZE)

    Path(f"{name}_public.pem").write_bytes(pub.save_pkcs1())
    Path(f"{name}_private.pem").write_bytes(priv.save_pkcs1())
    print(f"  Saved {name}_public.pem and {name}_private.pem")

print("\nDone. Keep the private keys secure and do not commit them to git.")