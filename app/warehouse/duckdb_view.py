"""DuckDB read-only analytical sandbox over Postgres data."""
import duckdb

from app.config import settings


def get_duckdb_conn() -> duckdb.DuckDBPyConnection:
    """Return a read-only DuckDB connection attached to Postgres via postgres_scanner."""
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
    url = settings.database_url
    url = url.replace("postgresql://", "").replace("postgres://", "")
    user_pw, rest = url.split("@", 1)
    user, pw = user_pw.split(":", 1)
    host_port, db = rest.split("/", 1)
    if ":" in host_port:
        host, port = host_port.split(":", 1)
    else:
        host, port = host_port, "5432"
    return {"user": user, "pw": pw, "host": host, "port": port, "db": db}


def sandboxed_sql(query: str, merchant_id: str) -> tuple[list[dict], list[str]]:
    """Execute a SELECT-only query against the warehouse. Returns (rows, provenance_ids)."""
    _validate_query(query)
    conn = get_duckdb_conn()
    try:
        result = conn.execute(query).fetchdf()
        rows = result.to_dict(orient="records")
        prov_ids = []
        if "provenance_ids" in result.columns:
            for row in rows:
                ids = row.pop("provenance_ids", []) or []
                prov_ids.extend(ids)
        return rows, list(set(prov_ids))
    finally:
        conn.close()


def _validate_query(query: str):
    """Reject anything that isn't a SELECT."""
    normalized = query.strip().upper()
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
                 "TRUNCATE", "COPY", "GRANT", "REVOKE", "EXECUTE"]
    if not normalized.startswith("SELECT") and not normalized.startswith("WITH"):
        raise ValueError("Only SELECT queries are allowed in the SQL sandbox.")
    for kw in forbidden:
        if f" {kw} " in f" {normalized} ":
            raise ValueError(f"Forbidden keyword '{kw}' in query.")
