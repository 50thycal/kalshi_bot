from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return key, pem


@pytest.fixture
def base_env(rsa_keypair, monkeypatch, tmp_path):
    _, pem = rsa_keypair
    monkeypatch.setenv("KALSHI_ENV", "demo")
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", pem)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    from kalshi_bot.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(base_env):
    from kalshi_bot.config import Settings

    return Settings(_env_file=None)
