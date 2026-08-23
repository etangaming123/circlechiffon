import json
import os
from pathlib import Path

import crypto_utils

# anchored to this file's own directory - see crypto_utils.KEY_FILE's comment
# for why a bare relative filename is a Windows-launch-context hazard.
_BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = str(_BASE_DIR / "config.json")

_DEFAULTS = {
    "token": "your bot token here",
    "owner_id": "your discord user id here (optional, for admin commands)",
    "db_path": "circlechiffon.db",
}


def _ensure_config_file():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w") as f:
            json.dump(_DEFAULTS, f, indent=4)
        input(
            f"Created {CONFIG_PATH} with default values. Please edit it with your "
            "bot token, then press enter to continue..."
        )


def _load_raw() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


class Config:
    def __init__(self):
        _ensure_config_file()
        data = _load_raw()

        stored_token = data.get("token", _DEFAULTS["token"])
        try:
            plaintext, updated = crypto_utils.resolve_and_upgrade(stored_token)
        except Exception:
            # not valid ciphertext yet -> treat as plaintext and encrypt at rest
            plaintext = stored_token
            updated = crypto_utils.encrypt_value(plaintext)

        if updated is not None:
            data["token"] = updated
            with open(CONFIG_PATH, "w") as f:
                json.dump(data, f, indent=4)
            print(f"Encrypted bot token at rest in {CONFIG_PATH}.")

        self.token = plaintext
        self.owner_id = data.get("owner_id")
        # a relative db_path (including the shipped default) is anchored here
        # rather than left to resolve against the process's CWD, same reason
        # as CONFIG_PATH/KEY_FILE above - an absolute path the user set
        # explicitly is left untouched.
        raw_db_path = data.get("db_path", "circlechiffon.db")
        self.db_path = raw_db_path if os.path.isabs(raw_db_path) else str(_BASE_DIR / raw_db_path)


config = Config()
