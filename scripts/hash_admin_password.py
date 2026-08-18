"""Generate the Railway secret used by NimHunt's admin panel."""

from __future__ import annotations

import getpass

from admin_auth import ADMIN_PASSWORD_HASH_ENV, hash_admin_password


def main() -> None:
    password = getpass.getpass("Choose NimHunt admin password (16+ characters): ")
    confirmation = getpass.getpass("Repeat admin password: ")
    if password != confirmation:
        raise SystemExit("Passwords did not match.")
    try:
        encoded = hash_admin_password(password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print()
    print(f"Add this Railway variable as {ADMIN_PASSWORD_HASH_ENV}:")
    print(encoded)
    print()
    print("The plaintext password is not stored in the generated value.")


if __name__ == "__main__":
    main()
