import sys
from sqlalchemy import select
from .auth import hasher
from .config import secret, cipher
from .db import Session, write_lock
from .models import Settings, User
from .password_policy import password_error


def bootstrap():
    cipher()  # Fail closed on missing/invalid encryption key.
    with Session.begin() as db:
        write_lock(db)
        if not db.get(Settings, 1):
            db.add(Settings(id=1))
        if not db.scalar(select(User.id).limit(1)):
            password = secret("BOOTSTRAP_PASSWORD")
            if password_error(password, username="admin", display_name="管理员"):
                raise RuntimeError(
                    "Bootstrap password does not meet the password policy"
                )
            db.add(
                User(
                    username="admin",
                    display_name="管理员",
                    role="admin",
                    password_hash=hasher.hash(password),
                    must_change_password=True,
                )
            )
    print("Database bootstrap complete; existing users and secrets were preserved.")


if __name__ == "__main__":
    if sys.argv[1:] == ["bootstrap"]:
        bootstrap()
    else:
        raise SystemExit("Usage: python -m app.cli bootstrap")
