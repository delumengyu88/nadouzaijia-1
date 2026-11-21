from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class PasswordHasher:
    """Argon2id hashes. Parameters stay modest so a login storm cannot exhaust the host."""

    TIME_COST = 2
    MEMORY_COST = 19456
    PARALLELISM = 1

    @staticmethod
    def hash(password: str) -> str:
        return PasswordHasher._hasher().hash(password)

    @staticmethod
    def verify(password_hash: str, password: str) -> bool:
        try:
            return PasswordHasher._hasher().verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    @staticmethod
    def _hasher() -> Argon2Hasher:
        return Argon2Hasher(
            time_cost=PasswordHasher.TIME_COST,
            memory_cost=PasswordHasher.MEMORY_COST,
            parallelism=PasswordHasher.PARALLELISM,
        )
