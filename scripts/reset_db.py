"""Drop and recreate all tables (as the owner role), then lock the audit log.

Usage: .venv\\Scripts\\python.exe -m scripts.reset_db [--test]
"""

import argparse
import re

from sqlalchemy import text
from sqlalchemy.engine import URL

import app.models  # noqa: F401  (registers tables)
from app.config import get_settings
from app.db import Base, make_engine


def reset(url: URL, app_user: str) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", app_user):
        raise ValueError(f"Unexpected app role name: {app_user!r}")

    engine = make_engine(url)
    with engine.begin() as conn:
        Base.metadata.drop_all(conn)
        Base.metadata.create_all(conn)
        # R10: the running API may only append audit rows.
        conn.execute(text(f'REVOKE UPDATE, DELETE, TRUNCATE ON platform.audit_log FROM "{app_user}"'))
    engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="reset the test database instead")
    args = parser.parse_args()

    settings = get_settings()
    url = settings.test_database_url if args.test else settings.owner_database_url
    reset(url, settings.db_app_user)
    print(f"Reset complete: {url.database} ({len(Base.metadata.tables)} tables)")


if __name__ == "__main__":
    main()
