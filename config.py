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
    "chart_render": "owner",
    "chart_render_server": "",
    "chart_render_key": "",
}


def _resolve_secret(stored: str) -> tuple[str, str | None]:
    """(plaintext, ciphertext to write back or None) for a value kept
    encrypted at rest. A value that isn't ciphertext yet is plaintext the
    user just typed in: it gets encrypted on this load."""
    try:
        return crypto_utils.resolve_and_upgrade(stored)
    except Exception:
        return stored, crypto_utils.encrypt_value(stored)


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

        plaintext, updated = _resolve_secret(data.get("token", _DEFAULTS["token"]))
        if updated is not None:
            data["token"] = updated
            with open(CONFIG_PATH, "w") as f:
                json.dump(data, f, indent=4)
            print(f"Encrypted bot token at rest in {CONFIG_PATH}.")

        # The shared secret for the remote chart renderer (render_server.py),
        # encrypted at rest like the token. Empty means no remote renderer.
        render_key = str(data.get("chart_render_key", "") or "")
        if render_key:
            render_key, updated = _resolve_secret(render_key)
            if updated is not None:
                data["chart_render_key"] = updated
                with open(CONFIG_PATH, "w") as f:
                    json.dump(data, f, indent=4)
                print(f"Encrypted chart_render_key at rest in {CONFIG_PATH}.")

        self.token = plaintext
        self.owner_id = data.get("owner_id")
        # a relative db_path (including the shipped default) is anchored here
        # rather than left to resolve against the process's CWD, same reason
        # as CONFIG_PATH/KEY_FILE above - an absolute path the user set
        # explicitly is left untouched.
        raw_db_path = data.get("db_path", "circlechiffon.db")
        self.db_path = raw_db_path if os.path.isabs(raw_db_path) else str(_BASE_DIR / raw_db_path)
        # Who may render /cc-chart videos: "owner" (the default) or "everyone".
        # Anyone else still gets the chart lookup.
        self.chart_render = str(data.get("chart_render", "owner")).strip().lower()
        # Where /cc-chart renders: "" renders on this machine, or the base URL
        # of a render_server.py on another one (e.g. "http://192.168.1.50:8765").
        # If that server can't be reached, renders fall back to this machine.
        self.chart_render_server = str(data.get("chart_render_server", "") or "").strip().rstrip("/")
        self.chart_render_key = render_key


config = Config()
