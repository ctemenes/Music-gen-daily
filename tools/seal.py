#!/usr/bin/env python3
"""Encrypt a file for one reader: a fresh Fernet key wrapped with the reader's RSA public key.

  seal.py seal   <pubkey_b64> <in> <out>     (Actions: the repo is public, so private reports go in sealed)
  seal.py open   <privkey.pem> <in> <out>    (the Claude session that made the key pair)
"""
import base64
import json
import sys

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)

cmd, key, src, dst = sys.argv[1:5]
if cmd == "seal":
    pub = serialization.load_pem_public_key(base64.b64decode(key))
    k = Fernet.generate_key()
    blob = {"key": base64.b64encode(pub.encrypt(k, OAEP)).decode(),
            "data": Fernet(k).encrypt(open(src, "rb").read()).decode()}
    open(dst, "w").write(json.dumps(blob))
else:
    priv = serialization.load_pem_private_key(open(key, "rb").read(), password=None)
    blob = json.load(open(src))
    k = priv.decrypt(base64.b64decode(blob["key"]), OAEP)
    open(dst, "wb").write(Fernet(k).decrypt(blob["data"].encode()))
