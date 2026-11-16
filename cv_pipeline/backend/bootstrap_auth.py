"""
One-time setup of the Talent Pool login: the first user and the Talent Pool admin password.

    python bootstrap_auth.py                           # asks for each value
    python bootstrap_auth.py --reset-admin-password    # replace a forgotten admin password

Values can also come from environment variables, for automation:
    BOOTSTRAP_USERNAME, BOOTSTRAP_DISPLAY_NAME, BOOTSTRAP_PASSWORD, BOOTSTRAP_ADMIN_PASSWORD

Safe to run again: an existing user or admin password is left alone. Further users are added
from the Talent Pool admin page.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import func, select

from app.auth import service
from app.auth.security import hash_password
from app.core.config import settings
from app.db.models import User
from app.db.session import session_scope


def _ask(env: str, prompt: str, *, secret: bool = False) -> str:
    value = os.environ.get(env)
    if value:
        return value
    while True:
        if secret:
            first = getpass.getpass(f"{prompt}: ")
            if first != getpass.getpass(f"{prompt} (again): "):
                print("  The values didn't match, try again.")
                continue
            value = first
        else:
            value = input(f"{prompt}: ").strip()
        if value:
            return value


def _check_password(value: str, label: str) -> None:
    if len(value) < settings.password_min_length:
        sys.exit(f"{label} must be at least {settings.password_min_length} characters.")


def run(reset_admin_password: bool = False) -> list[str]:
    done = []
    with session_scope() as db:
        if db.scalar(select(func.count(User.id))) == 0:
            print("Creating the first Talent Pool user.")
            username = _ask("BOOTSTRAP_USERNAME", "Username (3-50 letters, digits, . _ -)")
            if not service.USERNAME_PATTERN.fullmatch(username):
                sys.exit("The username must be 3-50 characters: letters, digits, dot, underscore or hyphen.")
            display_name = _ask("BOOTSTRAP_DISPLAY_NAME", "Display name")
            password = _ask("BOOTSTRAP_PASSWORD", "Password", secret=True)
            _check_password(password, "The password")
            db.add(User(username=username, display_name=display_name, password_hash=hash_password(password)))
            done.append(f"created user {username}")
        else:
            done.append("users already exist, none created")

    with session_scope() as db:
        if reset_admin_password or not service.admin_password_configured(db):
            password = _ask("BOOTSTRAP_ADMIN_PASSWORD", "Talent Pool admin password", secret=True)
            _check_password(password, "The admin password")
            service.set_admin_password(db, password)
            done.append("set the admin password")
        else:
            done.append("admin password already set, unchanged")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset-admin-password", action="store_true")
    for line in run(parser.parse_args().reset_admin_password):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
