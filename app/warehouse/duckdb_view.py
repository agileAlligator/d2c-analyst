"""DuckDB read-only analytical sandbox over Postgres data."""
import logging
import re

import duckdb

from app.config import settings

logger = logging.getLogger(__name__)


def get_duckdb_conn() -> duckdb.DuckDBPyConnection:
    """Return a DuckDB connection attached to Postgres via postgres_scanner.

    Security: _validate_query blocks all external-read tokens (READ_CSV, GLOB, etc.)
    and write/DDL tokens. enable_external_access=false is intentionally omitted because
    it also blocks LOAD and ATTACH TCP connections, breaking the postgres extension.
    """
    conn = duckdb.connect(database=":memory:")
    conn.execute("INSTALL postgres; LOAD postgres;")
    p = _parse_url()
    attach_str = (
        f"dbname={p['db']} host={p['host']} port={p['port']} "
        f"user={p['user']} password={p['pw']}"
    )
    conn.execute(f"ATTACH '{attach_str}' AS pg (TYPE POSTGRES, READ_ONLY)")
    return conn


def _parse_url() -> dict:
    # postgresql://user:pw@host:port/db
    # Use the analytics URL (superuser) so DuckDB's own Postgres connections can
    # read past RLS.  Merchant isolation is enforced by the view-layer WHERE
    # clause in sandboxed_sql() — see settings.database_url_analytics docstring.
    url = settings.database_url_analytics
    url = url.replace("postgresql://", "").replace("postgres://", "")
    user_pw, rest = url.split("@", 1)
    user, pw = user_pw.split(":", 1)
    host_port, db = rest.split("/", 1)
    if ":" in host_port:
        host, port = host_port.split(":", 1)
    else:
        host, port = host_port, "5432"
    return {"user": user, "pw": pw, "host": host, "port": port, "db": db}


def _coerce_id_list(value) -> list[str]:
    if value is None:
        return []
    # numpy array: check via .size/.tolist to avoid __bool__ ambiguity
    if hasattr(value, "size") and hasattr(value, "tolist"):
        if value.size == 0:
            return []
        return [str(x) for x in value.tolist() if x is not None]
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if x is not None]
    # pandas NA / float nan
    try:
        import pandas as pd
        if pd.isna(value):
            return []
    except (TypeError, ValueError, ImportError):
        pass
    return [str(value)]


def sandboxed_sql(query: str, merchant_id: str) -> tuple[list[dict], list[str]]:
    """Execute a SELECT-only query against the warehouse. Returns (rows, provenance_ids).

    Merchant isolation is enforced via temporary views that shadow each warehouse table
    with a WHERE merchant_id = '<merchant_id>' filter, so callers do not need to include
    explicit merchant_id predicates. Queries referencing :merchant_id are also supported
    (the placeholder is replaced with the literal value before execution).
    """
    _validate_query(query)
    quoted_mid = merchant_id.replace("'", "''")
    # Replace :merchant_id placeholder if the query uses it explicitly
    query_exec = re.sub(r":merchant_id\b", f"'{quoted_mid}'", query)

    conn = get_duckdb_conn()
    try:
        # Shadow each warehouse table with a merchant-scoped view so all queries
        # are automatically filtered to this merchant even without explicit predicates.
        for tbl in ("entities", "events", "links", "provenance"):
            conn.execute(
                f"CREATE OR REPLACE TEMP VIEW {tbl} AS "
                f"SELECT * FROM pg.{tbl} WHERE merchant_id = '{quoted_mid}'"
            )
        result = conn.execute(query_exec).fetchdf()
        rows = result.to_dict(orient="records")
        prov_ids: list[str] = []
        if "provenance_ids" in result.columns:
            for row in rows:
                ids = _coerce_id_list(row.pop("provenance_ids", None))
                prov_ids.extend(ids)
        return rows, sorted(set(prov_ids))
    finally:
        conn.close()


_FORBIDDEN_TOKENS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "COPY",
    "GRANT", "REVOKE", "EXECUTE", "ATTACH", "DETACH", "CALL", "PRAGMA",
    "INSTALL", "LOAD", "IMPORT", "EXPORT", "READ_CSV", "READ_JSON",
    "READ_PARQUET", "PARQUET_SCAN", "READ_TEXT", "READ_BLOB", "GLOB", "WRITE_CSV",
    # DuckDB introspection functions that expose credentials/secrets
    "DUCKDB_SETTINGS", "DUCKDB_SECRETS", "DUCKDB_EXTENSIONS",
    "DUCKDB_COLUMNS", "DUCKDB_TABLES", "DUCKDB_VIEWS",
}


def _validate_query(query: str) -> None:
    """Reject anything that isn't a safe SELECT targeting warehouse tables."""
    stripped = query.strip().rstrip(";")
    if ";" in stripped:
        raise ValueError("Multiple statements not allowed")
    tokens = set(re.split(r"\W+", query.upper()))
    forbidden_found = tokens & _FORBIDDEN_TOKENS
    if forbidden_found:
        raise ValueError(f"Forbidden SQL tokens: {forbidden_found}")
    # Block schema-qualified pg access in all forms: pg.table, "pg".table, `pg`.table
    if re.search(r'(?:\bpg\b|"pg"|`pg`)\s*\.', query, re.IGNORECASE):
        raise ValueError("Direct schema access (pg.) not permitted — use bare table names")
