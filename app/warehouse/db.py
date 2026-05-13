import re

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# Postgres information_schema verbose type → short alias
_TYPE_MAP = {
    "uuid": "UUID",
    "text": "TEXT",
    "character varying": "TEXT",
    "integer": "INT",
    "bigint": "BIGINT",
    "numeric": "NUMERIC",
    "double precision": "FLOAT",
    "real": "FLOAT",
    "boolean": "BOOL",
    "jsonb": "JSONB",
    "json": "JSON",
    "timestamp with time zone": "TIMESTAMPTZ",
    "timestamp without time zone": "TIMESTAMP",
    "date": "DATE",
}

_WAREHOUSE_TABLES = ("entities", "events", "provenance", "links")
_SKIP_COLS = {"merchant_id"}  # always present on every table — omit for brevity


def get_schema_description() -> str:
    """Return a single-line-per-table column listing queried live from the DB."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ANY(:tables)
            ORDER BY table_name, ordinal_position
        """), {"tables": list(_WAREHOUSE_TABLES)}).fetchall()

    tables: dict[str, list[str]] = {}
    for table_name, col_name, data_type in rows:
        if col_name in _SKIP_COLS:
            continue
        short_type = _TYPE_MAP.get(data_type, data_type.upper())
        tables.setdefault(table_name, []).append(f"{col_name} {short_type}")

    lines = []
    for tbl in _WAREHOUSE_TABLES:
        if tbl in tables:
            lines.append(f"  {tbl}({', '.join(tables[tbl])})")
    return "\n".join(lines)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_MERCHANT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def set_merchant(db: Session, merchant_id: str) -> None:
    if not merchant_id or not _MERCHANT_ID_RE.match(merchant_id):
        raise ValueError(f"Invalid merchant_id: {merchant_id!r}")
    db.execute(
        text("SELECT set_config('app.current_merchant', :mid, true)"),
        {"mid": merchant_id},
    )
