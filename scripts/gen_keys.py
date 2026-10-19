#!/usr/bin/env python3
"""Print a fresh FERNET_KEY and SESSION_SECRET for .env (FR-1).

Prints only; never reads or modifies .env. Copy the lines into .env yourself.
"""

from __future__ import annotations

import base64
import os
import secrets


def generate_fernet_key() -> str:
    # Same format as cryptography.fernet.Fernet.generate_key(): urlsafe b64 of 32 bytes.
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def generate_session_secret() -> str:
    return secrets.token_urlsafe(48)


def main() -> None:
    print(f"FERNET_KEY={generate_fernet_key()}")
    print(f"SESSION_SECRET={generate_session_secret()}")


if __name__ == "__main__":
    main()
