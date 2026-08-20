from __future__ import annotations

import getpass
import json
import secrets
from typing import Any

from argon2 import PasswordHasher


def build_secret(
    admin_password: str, user_password: str, *, session_key: str | None = None
) -> dict[str, Any]:
    if len(admin_password) < 12 or len(user_password) < 12:
        raise ValueError("each password must contain at least 12 characters")
    hasher = PasswordHasher()
    return {
        "accounts": [
            {
                "username": "admin",
                "role": "admin",
                "password_hash": hasher.hash(admin_password),
            },
            {
                "username": "user",
                "role": "user",
                "password_hash": hasher.hash(user_password),
            },
        ],
        "session_key": session_key or secrets.token_urlsafe(48),
        "auth_version": 1,
    }


def _read_twice(label: str) -> str:
    first = getpass.getpass(f"{label} password: ")
    second = getpass.getpass(f"Confirm {label} password: ")
    if first != second:
        raise ValueError(f"{label} passwords do not match")
    return first


def main() -> None:
    print(
        json.dumps(
            build_secret(_read_twice("admin"), _read_twice("user")),
            separators=(",", ":"),
        )
    )
