"""Server-local password recovery; never creates accounts or prints passwords."""

import argparse
import getpass
import os
from pathlib import Path

from .store import Store, password_hash


def main():
    parser = argparse.ArgumentParser(description="Reset an existing workbench password on this server")
    parser.add_argument("username")
    args = parser.parse_args()
    store = Store(Path(os.environ.get("DESK_DATA_DIR", Path(__file__).resolve().parent.parent / ".data")))
    user = store.one("SELECT id FROM users WHERE username=?", (args.username,))
    if not user:
        raise SystemExit("User does not exist.")
    password = getpass.getpass("New password (at least 10 characters): ")
    if len(password) < 10:
        raise SystemExit("Password must contain at least 10 characters.")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match.")
    with store.connect() as db:
        db.execute("UPDATE users SET password=?,active=1 WHERE id=?", (password_hash(password), user["id"]))
        db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    print("Password updated. Existing sessions have been revoked.")


if __name__ == "__main__":
    main()
