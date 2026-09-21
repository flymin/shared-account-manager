import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from sqlalchemy.engine import URL


def parse_public_origin(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or any(char.isspace() for char in value)
    ):
        raise ValueError("PUBLIC_ORIGIN must be an http(s) origin without a path")
    _ = parsed.port  # Validate the optional port before the application starts.
    return value


PUBLIC_ORIGIN = parse_public_origin(os.getenv("PUBLIC_ORIGIN", ""))


def secret(name: str) -> str:
    path = os.environ.get(f"{name}_FILE")
    if not path:
        raise RuntimeError(f"{name}_FILE must name a persistent secret file")
    return Path(path).read_text().strip()


def database_url():
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    return URL.create(
        "postgresql+psycopg",
        username="account_manager",
        password=secret("DB_PASSWORD"),
        host=os.getenv("DB_HOST", "db"),
        database=os.getenv("DB_NAME", "account_manager"),
    )


@lru_cache
def cipher():
    return Fernet(secret("CREDENTIAL_KEY").encode())
