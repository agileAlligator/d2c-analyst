"""Idempotent: create d2c_app role (NOSUPERUSER NOBYPASSRLS) and grant minimum privileges.

Run via: DATABASE_URL=postgresql://d2c:d2c@localhost:5434/d2c python -m app.warehouse.migrations.create_app_role
Must be run with superuser credentials (d2c); the app then connects as d2c_app.
"""
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)
APP_ROLE = "d2c_app"
APP_PASSWORD = "d2c_app"


def run(engine):
    with engine.begin() as conn:
        conn.execute(text(f"""
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'
                  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
              END IF;
            END
            $$;
        """))
        conn.execute(text(f"GRANT CONNECT ON DATABASE d2c TO {APP_ROLE}"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        conn.execute(text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
        ))
        conn.execute(text(
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"
        ))
        conn.execute(text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
        ))
    logger.info("App role '%s' ensured (NOSUPERUSER NOBYPASSRLS).", APP_ROLE)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
    from sqlalchemy import create_engine
    import os
    url = os.environ.get("DATABASE_URL", "postgresql://d2c:d2c@localhost:5434/d2c")
    eng = create_engine(url)
    run(eng)
    print(f"Role {APP_ROLE} created/verified.")
