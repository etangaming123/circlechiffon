"""
Standalone key generator for the env-var encryption key used by
crypto_utils.py. Portable - works in any repo that uses the same
env-var-first key scheme, just pass the var names you actually use there.

Usage:
    python3 generate_env_keys.py                                  # CIRCLECHIFFON_ENCRYPTION_KEY
    python3 generate_env_keys.py MY_VAR_NAME another_var           # any var names you want
    python3 generate_env_keys.py --apply                           # Windows only: sets them via `setx` instead of printing export/set lines

Without --apply, this only prints ready-to-paste lines (export on
macOS/Linux, setx on Windows) - one independent key per var name, never
reusing a key across vars. Nothing is written to disk either way; setx
stores the value in the Windows user environment (registry), not a file.
"""

import os
import sys
import subprocess
from cryptography.fernet import Fernet

DEFAULT_VARS = ["CIRCLECHIFFON_ENCRYPTION_KEY"]

def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    var_names = [a for a in args if not a.startswith("--")] or DEFAULT_VARS

    keys = {name: Fernet.generate_key().decode("utf-8") for name in var_names}

    if apply and os.name == "nt":
        for name, key in keys.items():
            subprocess.run(["setx", name, key], check=True)
        print()
        print("Set. Open a new terminal (or just start the bot normally) for these to take effect.")
        print("Back these up somewhere safe - if lost, you'll need to relink any accounts and re-enter your bot token:")
        for name, key in keys.items():
            print(f"  {name} = {key}")
        return

    if apply:
        print("--apply only automates setx on Windows. On this platform, add these to your shell profile:")

    print("# Each key is independent - do not reuse one across variables.")
    for name, key in keys.items():
        if os.name == "nt":
            print(f'setx {name} "{key}"')
        else:
            print(f'export {name}="{key}"')

if __name__ == "__main__":
    main()
