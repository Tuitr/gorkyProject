"""Argon2id password hashes through argon2-cffi; the code never compares passwords itself.

Each hash carries its own random salt and the parameters it was made with. A check of an unknown login
still runs one verification against a dummy hash, so both refusals cost the same time.
"""

import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError


class Passwords:
    def __init__(self, hasher: PasswordHasher | None = None) -> None:
        self.hasher = hasher if hasher is not None else PasswordHasher(type=Type.ID)
        if self.hasher.type is not Type.ID:
            raise ValueError("teacher passwords are hashed with Argon2id")
        self.dummyHash = self.hasher.hash(secrets.token_urlsafe(32))

    def hash(self, password: str) -> str:
        return self.hasher.hash(password)

    def verify(self, storedHash: str | None, password: str) -> bool:
        """False for a wrong password and for a missing account, after the same amount of work."""
        try:
            matches = self.hasher.verify(storedHash if storedHash is not None else self.dummyHash, password)
        except (VerificationError, InvalidHashError):
            return False
        return matches and storedHash is not None

    def needsRehash(self, storedHash: str) -> bool:
        return self.hasher.check_needs_rehash(storedHash)
