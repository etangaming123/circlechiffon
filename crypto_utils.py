import os
from pathlib import Path

from cryptography.fernet import Fernet

# anchored to this file's own directory rather than a bare relative filename -
# a bare name resolves against the process's current working directory, which
# on Windows can silently differ from the repo directory depending on launch
# method (desktop shortcut, Task Scheduler, a service wrapper, etc.), causing
# a brand-new key to be generated and every previously-encrypted value (the
# session cookie, stored credentials) to become undecryptable.
KEY_FILE = str(Path(__file__).resolve().parent / ".circlechiffon.key")
ENV_KEY_VAR = "CIRCLECHIFFON_ENCRYPTION_KEY"

_env_fernet = None
_env_fernet_checked = False
_file_fernet = None


def _load_or_create_key() -> bytes:
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    with open(KEY_FILE, "wb") as f:
        f.write(key)
    try:
        os.chmod(KEY_FILE, 0o600)
    except Exception:
        pass
    print(f"Generated new encryption key at [{KEY_FILE}]. Set {ENV_KEY_VAR} instead to avoid keeping the key on disk at all.")
    return key


def _get_env_fernet():
    global _env_fernet, _env_fernet_checked
    if not _env_fernet_checked:
        val = os.environ.get(ENV_KEY_VAR)
        _env_fernet = Fernet(val.encode("utf-8")) if val else None
        _env_fernet_checked = True
    return _env_fernet


def _get_file_fernet() -> Fernet:
    global _file_fernet
    if _file_fernet is None:
        _file_fernet = Fernet(_load_or_create_key())
    return _file_fernet


def _active_fernet() -> Fernet:
    return _get_env_fernet() or _get_file_fernet()


def encrypt_value(plaintext: str) -> str:
    return _active_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(ciphertext: str) -> str:
    return _active_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def resolve_and_upgrade(stored: str):
    """Decrypt `stored`, returning (plaintext, updated_ciphertext). updated_ciphertext
    is None if `stored` was already under the active key, otherwise it's the
    re-encrypted value the caller should persist."""
    active = _active_fernet()

    try:
        return active.decrypt(stored.encode("utf-8")).decode("utf-8"), None
    except Exception:
        pass

    # only worth trying the old on-disk key if the env key is what's active
    # now and a leftover key file exists (never create one just to check)
    if _get_env_fernet() is not None and os.path.exists(KEY_FILE):
        try:
            plaintext = _get_file_fernet().decrypt(stored.encode("utf-8")).decode("utf-8")
            return plaintext, active.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        except Exception:
            pass

    raise ValueError("Unable to decrypt stored value under any known key")
