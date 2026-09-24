from cryptography.fernet import Fernet

from app.config import settings


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def _fernet() -> Fernet:
    if not settings.config_encryption_key:
        raise RuntimeError("WG_CONFIG_ENCRYPTION_KEY must be set for secrets and client configs")
    try:
        return Fernet(settings.config_encryption_key.encode())
    except (ValueError, TypeError) as exc:
        raise RuntimeError("WG_CONFIG_ENCRYPTION_KEY must be a valid Fernet key") from exc
