"""Генерирует пару VAPID-ключей для web push и печатает строки для .env."""

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


key = ec.generate_private_key(ec.SECP256R1())
private = key.private_numbers().private_value.to_bytes(32, "big")
public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
print(f"VAPID_PRIVATE_KEY={b64url(private)}")
print(f"VAPID_PUBLIC_KEY={b64url(public)}")
