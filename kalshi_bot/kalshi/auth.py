"""Kalshi request signing.

Each authenticated request carries three headers:
  KALSHI-ACCESS-KEY        the API key id
  KALSHI-ACCESS-TIMESTAMP  current time in milliseconds (string)
  KALSHI-ACCESS-SIGNATURE  base64(RSA-PSS-SHA256 over `timestamp + METHOD + path`)

`path` is the full request path including the `/trade-api/v2` prefix, with any
query string stripped. RSA-PSS uses SHA-256 for the digest and MGF1, with the
salt length equal to the digest length (32 bytes).
"""

from __future__ import annotations

import base64
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class KalshiSigner:
    def __init__(self, api_key_id: str, private_key_pem: str):
        self.api_key_id = api_key_id
        self._key = self._load_key(private_key_pem)

    @staticmethod
    def _load_key(pem: str) -> rsa.RSAPrivateKey:
        # Tolerate single-line env values that escape newlines as literal "\n".
        if "\\n" in pem and "\n" not in pem:
            pem = pem.replace("\\n", "\n")
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("Kalshi private key must be an RSA private key")
        return key

    @staticmethod
    def _timestamp_ms() -> str:
        return str(int(time.time() * 1000))

    def sign(self, timestamp_ms: str, method: str, path: str) -> str:
        message = f"{timestamp_ms}{method.upper()}{path}".encode()
        signature = self._key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("ascii")

    def auth_headers(self, method: str, path: str) -> dict[str, str]:
        ts = self._timestamp_ms()
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": self.sign(ts, method, path),
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }
