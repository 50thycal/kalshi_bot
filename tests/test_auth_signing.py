from __future__ import annotations

import base64

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from kalshi_bot.kalshi.auth import KalshiSigner


def _verify(public_key, signature_b64: str, message: str) -> None:
    public_key.verify(
        base64.b64decode(signature_b64),
        message.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_signature_verifies_against_public_key(rsa_keypair):
    key, pem = rsa_keypair
    signer = KalshiSigner("kid", pem)
    ts, method, path = "1700000000000", "GET", "/trade-api/v2/portfolio/balance"
    sig = signer.sign(ts, method, path)
    # Raises InvalidSignature if the scheme/message doesn't match.
    _verify(key.public_key(), sig, f"{ts}{method}{path}")


def test_auth_headers_present(rsa_keypair):
    _, pem = rsa_keypair
    headers = KalshiSigner("kid", pem).auth_headers("GET", "/trade-api/v2/exchange/status")
    assert headers["KALSHI-ACCESS-KEY"] == "kid"
    assert headers["KALSHI-ACCESS-SIGNATURE"]
    assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()


def test_escaped_newline_pem_loads(rsa_keypair):
    key, pem = rsa_keypair
    signer = KalshiSigner("kid", pem.replace("\n", "\\n"))
    ts, method, path = "1", "GET", "/trade-api/v2/exchange/status"
    _verify(key.public_key(), signer.sign(ts, method, path), f"{ts}{method}{path}")
