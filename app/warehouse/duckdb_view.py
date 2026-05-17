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
    attach_str = f"dbname={p['db']} host={p['host']} port={p['port']} user={p['user']} password={p['pw']}"
    conn.execute(f"ATTACH '{attach_str}' AS pg (TYPE POSTGRES, READ_ONLY)")
    # Disable DuckDB's local filesystem so path-literal replacement scans
    # (SELECT * FROM 'file.csv') cannot read host files.  The Postgres ATTACH
    # extension uses a TCP connection and is unaffected by this setting.
    conn.execute("SET disabled_filesystems='LocalFileSystem,HTTPFileSystem,S3FileSystem'")
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
                f"CREATE OR REPLACE TEMP VIEW {tbl} AS SELECT * FROM pg.{tbl} WHERE merchant_id = '{quoted_mid}'"
            )
        # Stub out internal operational tables with empty views so bare-name
        # references hit an empty result set rather than falling through to the
        # Postgres attachment (which uses the superuser and bypasses RLS).
        for internal_tbl in ("agent_runs", "ingest_cursors", "ingest_jobs"):
            conn.execute(
                f"CREATE OR REPLACE TEMP VIEW {internal_tbl} AS SELECT * FROM pg.public.{internal_tbl} WHERE FALSE"
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
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
    "COPY",
    "GRANT",
    "REVOKE",
    "EXECUTE",
    "ATTACH",
    "DETACH",
    "CALL",
    "PRAGMA",
    "INSTALL",
    "LOAD",
    "IMPORT",
    "EXPORT",
    "READ_CSV",
    "READ_JSON",
    "READ_PARQUET",
    "PARQUET_SCAN",
    "READ_TEXT",
    "READ_BLOB",
    "GLOB",
    "WRITE_CSV",
    # DuckDB auto-sniffing and format variants (underscore is \w so these are single tokens)
    "READ_CSV_AUTO",
    "READ_JSON_AUTO",
    "READ_NDJSON_AUTO",
    "READ_JSON_OBJECTS",
    "SNIFF_CSV",
    "ST_READ",
    "READ_PARQUET_AUTO",
    # DuckDB postgres extension functions — bypass merchant-scoped views entirely
    # by sending raw SQL directly to Postgres over the ATTACH'd connection.
    "POSTGRES_QUERY",
    "POSTGRES_EXECUTE",
    # postgres_scan / postgres_scan_pushdown open a NEW Postgres connection,
    # bypassing the merchant-scoped temp views entirely.
    "POSTGRES_SCAN",
    "POSTGRES_SCAN_PUSHDOWN",
    # SET can re-enable filesystems (SET disabled_filesystems='') or cause DoS
    # (SET memory_limit='1B'); RESET undoes hardened settings; USE switches schema.
    "SET",
    "RESET",
    "USE",
    # DuckDB introspection functions that expose credentials/secrets
    "DUCKDB_SETTINGS",
    "DUCKDB_SECRETS",
    "DUCKDB_EXTENSIONS",
    "DUCKDB_COLUMNS",
    "DUCKDB_TABLES",
    "DUCKDB_VIEWS",
    # DuckDB catalog functions not yet blocked
    "DUCKDB_DATABASES",
    "DUCKDB_SCHEMAS",
    "DUCKDB_FUNCTIONS",
    "DUCKDB_TYPES",
    "DUCKDB_CONSTRAINTS",
    "DUCKDB_INDEXES",
    "DUCKDB_KEYWORDS",
    "DUCKDB_TEMPORARY_FILES",
    "PRAGMA_DATABASE_LIST",
    "PRAGMA_TABLE_INFO",
    "PRAGMA_SHOW",
    # DuckDB Postgres/MySQL/SQLite ATTACH variants — open a new connection, bypassing
    # merchant-scoped views (SSRF / credential-exfil bypass)
    "POSTGRES_ATTACH",
    "MYSQL_ATTACH",
    "SQLITE_ATTACH",
    # EXPLAIN leaks physical table paths and pg attachment details
    "EXPLAIN",
    # File-read table functions for extension formats (Arrow, Avro, Iceberg, Delta, Excel)
    "READ_ARROW",
    "READ_AVRO",
    "READ_XLSX",
    "READ_EXCEL",
    "ICEBERG_SCAN",
    "DELTA_SCAN",
    "READ_ICEBERG",
    # Parquet metadata introspection functions (expose host filesystem paths)
    "PARQUET_METADATA",
    "PARQUET_SCHEMA",
    "PARQUET_KV_METADATA",
    "PARQUET_FILE_METADATA",
    # Postgres system catalogs exposed by DuckDB as bare names (no schema prefix needed)
    "PG_CLASS",
    "PG_DATABASE",
    "PG_NAMESPACE",
    "PG_SETTINGS",
    "PG_TABLES",
    "PG_VIEWS",
    # Additional PG catalog bare names usable for enumeration or SSRF
    "PG_PROC",
    "PG_ATTRIBUTE",
    "PG_TYPE",
    "PG_CONSTRAINT",
    "PG_INDEX",
    "PG_INDEXES",
    "PG_AUTHID",
    "PG_ROLES",
    "PG_USER",
    "PG_SHADOW",
    # SQL control statements (schema-revealing or connection-state-altering)
    "DESCRIBE",
    "SHOW",
    "SUMMARIZE",
    "CHECKPOINT",
    "VACUUM",
    "ANALYZE",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    # chr()/concat()/query()/query_table() enable dynamic SQL construction:
    # query(chr(83)||chr(69)||...) builds arbitrary SQL strings at runtime,
    # bypassing every token-level check. Block all of them.
    "QUERY",
    "QUERY_TABLE",
    "CHR",
    "CONCAT",
    # current_setting() exposes DuckDB internal config values one key at a time,
    # even though duckdb_settings() is blocked.
    "CURRENT_SETTING",
    # Additional file-read table functions (blocked at engine level by
    # disabled_filesystems too, but defense-in-depth blocklist entry is cheap).
    "READ_NDJSON",
    "READ_NDJSON_OBJECTS",
    "READ_JSON_OBJECTS_AUTO",
    "READ_DUCKDB",
    # Raw tables — not shadowed by merchant-scoped views, so unqualified references
    # would bypass merchant isolation by resolving against the Postgres attachment.
    "RAW_SHOPIFY_ORDERS",
    "RAW_SHOPIFY_REFUNDS",
    "RAW_SHOPIFY_PRODUCTS",
    "RAW_SHOPIFY_CUSTOMERS",
    "RAW_META_CAMPAIGNS",
    "RAW_META_ADSETS",
    "RAW_META_ADS",
    "RAW_META_INSIGHTS",
    "RAW_SHIPROCKET_SHIPMENTS",
}


def _strip_sql_comments(sql: str) -> str:
    """Remove SQL line comments (--) and block comments (/* */) from a query."""
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def _validate_query(query: str) -> None:
    """Reject anything that isn't a safe SELECT targeting warehouse tables."""
    # Reject comment markers and carriage returns before any stripping.
    # _strip_sql_comments is not string-context-aware; a /* inside one literal
    # and */ inside another can swallow a forbidden token that DuckDB still executes.
    if any(marker in query for marker in ("--", "/*", "*/", "\r")):
        raise ValueError("Query rejected: SQL comments and carriage returns are not permitted.")
    # Strip comments before all checks so that constructs like
    # pg/**/.entities or pg-- x\n.entities cannot bypass pattern matches.
    clean = _strip_sql_comments(query)
    stripped = clean.strip().rstrip(";")
    if ";" in stripped:
        raise ValueError("Multiple statements not allowed")
    # Block path-literal reads: FROM '/...' or FROM './...'
    # Absolute paths ('/etc/passwd') and dot-relative paths ('./file') are caught
    # here as defense-in-depth. Relative paths without a leading / or . (e.g.
    # 'data.csv') are blocked at the engine level by SET disabled_filesystems=
    # 'LocalFileSystem' in get_duckdb_conn(), so no regex alternative is needed
    # for them — the third alternative that matched bare extensions was too greedy
    # and produced false positives on innocuous string literals like 'report.json'.
    if re.search(r"'\s*/|'\s*\.", clean, re.IGNORECASE):
        raise ValueError("Query contains path literals which are not allowed")
    tokens = set(re.split(r"\W+", clean.upper()))
    forbidden_found = tokens & _FORBIDDEN_TOKENS
    if forbidden_found:
        raise ValueError(f"Forbidden SQL tokens: {forbidden_found}")
    # Block schema-qualified pg access in all forms: pg.table, "pg".table, `pg`.table,
    # pg_catalog.pg_settings, information_schema.tables, etc.
    if re.search(
        r'(?:\bpg\b|"pg"|`pg`)\s*\.'
        r'|(?:\bpg_catalog\b|"pg_catalog")\s*\.'
        r'|(?:\binformation_schema\b|"information_schema")\s*\.',
        clean,
        re.IGNORECASE,
    ):
        raise ValueError("Direct schema access not permitted — use bare table names")
