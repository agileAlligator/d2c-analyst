import re

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings

# NullPool: each Session gets a fresh Postgres connection, never reused.
# This lets us store the active merchant_id on the session and replay the
# app.current_merchant GUC at the start of every new transaction via after_begin.
# With a pooled engine, the GUC would reset when the connection is returned to
# the pool and silently re-acquired — requiring more complex bookkeeping.
# NullPool trades connection reuse for simpler GUC semantics; acceptable for
# the current scale (single-process, <<100 RPS).  At 10k merchants / high RPS,
# replace with a pool + connection-scoped GUC injection via a custom pool listener.
engine = create_engine(settings.database_url, poolclass=NullPool)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@event.listens_for(SessionLocal, "after_begin")
def _restore_merchant_after_begin(session, transaction, connection):
    """Replay app.current_merchant GUC at the start of every new transaction.

    Because NullPool closes and re-opens the Postgres connection on every
    transaction boundary, the GUC (even SET session-level) does not survive a
    Session.commit().  By storing the active merchant_id on the Session object
    and replaying it here, the GUC is always set correctly for the next query
    without callers needing to re-call set_merchant() after every commit.
    """
    mid = getattr(session, "_active_merchant_id", None)
    if mid:
        connection.execute(
            text("SELECT set_config('app.current_merchant', :mid, true)"),
            {"mid": mid},
        )


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
        rows = conn.execute(
            text("""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ANY(:tables)
            ORDER BY table_name, ordinal_position
        """),
            {"tables": list(_WAREHOUSE_TABLES)},
        ).fetchall()

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
    # Store on the session so after_begin can replay it on every new transaction.
    db._active_merchant_id = merchant_id  # type: ignore[attr-defined]
    db.execute(
        text("SELECT set_config('app.current_merchant', :mid, true)"),
        {"mid": merchant_id},
    )
