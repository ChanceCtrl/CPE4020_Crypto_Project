"""
DePIN ECDSA Keygen — run this once before starting your nodes.

Generates ECDSA P-256 keypairs for walletA and walletB and saves them.
The validator and test client load keys from these files at startup;
the Arduino's keys come out of extract_key_for_arduino.py.

Usage:
    pip install ecdsa --break-system-packages
    python keygen.py

Output files (per wallet):
    walletA.pub       — 64-byte raw public key (X || Y, uncompressed)
    walletA.pub.hex   — same as above, hex-encoded (used as walletKey)
    walletA.priv      — 32-byte raw private key (KEEP SECRET)
"""

from ecdsa import SigningKey, NIST256p
from pathlib import Path

WALLETS = ["walletA", "walletB"]

for name in WALLETS:
    print(f"Generating P-256 keypair for {name}...")
    sk = SigningKey.generate(curve=NIST256p)
    vk = sk.verifying_key

    priv_bytes = sk.to_string()              # 32 bytes
    pub_bytes  = vk.to_string()              # 64 bytes (X || Y)
    pub_hex    = pub_bytes.hex()             # 128 chars

    Path(f"{name}.priv").write_bytes(priv_bytes)
    Path(f"{name}.pub").write_bytes(pub_bytes)
    Path(f"{name}.pub.hex").write_text(pub_hex)
    print(f"  Saved {name}.priv (32 bytes), {name}.pub (64 bytes), {name}.pub.hex")

print("\nDone. Distribute walletA.pub.hex (and walletB.pub.hex) to all validator")
print("nodes. Keep .priv files secret — do not commit them to git.")