"""Passwords live in the OS credential store via keyring."""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError

from . import config


class SecretStoreError(RuntimeError):
    pass


def _key(org_id: int, username: str) -> str:
    return f"{org_id}:{username.strip().lower()}"


def set_password(org_id: int, username: str, password: str) -> None:
    try:
        keyring.set_password(config.KEYRING_SERVICE, _key(org_id, username), password)
    except KeyringError as e:
        raise SecretStoreError(f"could not store password in the system keychain: {e}") from e


def get_password(org_id: int, username: str) -> str | None:
    try:
        return keyring.get_password(config.KEYRING_SERVICE, _key(org_id, username))
    except KeyringError as e:
        raise SecretStoreError(f"could not read password from the system keychain: {e}") from e


def delete_password(org_id: int, username: str) -> None:
    try:
        keyring.delete_password(config.KEYRING_SERVICE, _key(org_id, username))
    except KeyringError:
        pass
