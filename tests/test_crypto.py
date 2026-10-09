"""crypto_utils.py - Fernet encryption at rest.

Every test runs against a throwaway key file in a temp folder and a clean
environment; the real .circlechiffon.key and CIRCLECHIFFON_ENCRYPTION_KEY are
never read or written.
"""

import os
import stat

import pytest
from cryptography.fernet import Fernet, InvalidToken

import crypto_utils

REAL_KEY_FILE = crypto_utils.KEY_FILE


@pytest.fixture(autouse=True)
def isolated_crypto(monkeypatch, tmp_path):
    key_file = str(tmp_path / "test.key")
    monkeypatch.setattr(crypto_utils, "KEY_FILE", key_file)
    monkeypatch.setattr(crypto_utils, "_file_fernet", None)
    monkeypatch.setattr(crypto_utils, "_env_fernet", None)
    monkeypatch.setattr(crypto_utils, "_env_fernet_checked", False)
    monkeypatch.delenv(crypto_utils.ENV_KEY_VAR, raising=False)
    assert crypto_utils.KEY_FILE != REAL_KEY_FILE
    return key_file


def use_env_key(monkeypatch) -> str:
    """Switches the active key to a fresh env-var key."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(crypto_utils.ENV_KEY_VAR, key)
    monkeypatch.setattr(crypto_utils, "_env_fernet", None)
    monkeypatch.setattr(crypto_utils, "_env_fernet_checked", False)
    return key


def test_round_trip():
    token = crypto_utils.encrypt_value("hunter2 パスワード")
    assert crypto_utils.decrypt_value(token) == "hunter2 パスワード"


def test_ciphertext_does_not_contain_plaintext():
    assert "hunter2" not in crypto_utils.encrypt_value("hunter2")


def test_same_plaintext_encrypts_differently_each_time():
    a, b = crypto_utils.encrypt_value("x"), crypto_utils.encrypt_value("x")
    assert a != b
    assert crypto_utils.decrypt_value(a) == crypto_utils.decrypt_value(b) == "x"


def test_key_file_is_created_on_first_use_and_owner_only(isolated_crypto):
    assert not os.path.exists(isolated_crypto)
    crypto_utils.encrypt_value("x")
    assert os.path.exists(isolated_crypto)
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(isolated_crypto).st_mode) == 0o600


def test_existing_key_file_is_reused(isolated_crypto, monkeypatch):
    token = crypto_utils.encrypt_value("secret")
    monkeypatch.setattr(crypto_utils, "_file_fernet", None)       # simulate a restart
    assert crypto_utils.decrypt_value(token) == "secret"


def test_env_key_wins_over_key_file(isolated_crypto, monkeypatch):
    key = use_env_key(monkeypatch)
    token = crypto_utils.encrypt_value("secret")
    assert Fernet(key.encode()).decrypt(token.encode()) == b"secret"
    assert not os.path.exists(isolated_crypto)                      # never created a file key


def test_wrong_key_cannot_decrypt(monkeypatch):
    token = crypto_utils.encrypt_value("secret")                   # under the file key
    use_env_key(monkeypatch)
    with pytest.raises(InvalidToken):
        crypto_utils.decrypt_value(token)


def test_resolve_current_key_needs_no_upgrade():
    token = crypto_utils.encrypt_value("secret")
    assert crypto_utils.resolve_and_upgrade(token) == ("secret", None)


def test_resolve_upgrades_file_key_value_to_env_key(monkeypatch):
    old = crypto_utils.encrypt_value("secret")                     # file key
    key = use_env_key(monkeypatch)                                  # now an env key is active
    plaintext, upgraded = crypto_utils.resolve_and_upgrade(old)
    assert plaintext == "secret"
    assert upgraded is not None and upgraded != old
    assert Fernet(key.encode()).decrypt(upgraded.encode()) == b"secret"
    assert crypto_utils.resolve_and_upgrade(upgraded) == ("secret", None)


def test_resolve_rejects_garbage():
    crypto_utils.encrypt_value("x")                                 # make sure a key exists
    with pytest.raises(ValueError):
        crypto_utils.resolve_and_upgrade("definitely not ciphertext")


def test_resolve_does_not_create_a_key_file_just_to_check(isolated_crypto, monkeypatch):
    use_env_key(monkeypatch)
    with pytest.raises(ValueError):
        crypto_utils.resolve_and_upgrade("not ciphertext")
    assert not os.path.exists(isolated_crypto)
