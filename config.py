import json
import os

import crypto_utils

CONFIG_PATH = "config.json"

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
        self.db_path = data.get("db_path", "circlechiffon.db")


config = Config()
