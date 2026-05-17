from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Runtime app-role URL (NOSUPERUSER NOBYPASSRLS) — used by the SQLAlchemy
    # session path (API, agent, normalizers).  RLS enforcement is in effect here.
    database_url: str = "postgresql://d2c_app:d2c_app@localhost:5434/d2c"

    # Read-only analytical URL for DuckDB sandbox queries.  DuckDB creates its
    # own Postgres connections and cannot set the app.current_merchant GUC, so
    # GUC-based RLS cannot apply.  Merchant isolation for this path is enforced
    # by the view-layer WHERE clause injected in sandboxed_sql().  Uses the
    # bootstrap superuser so RLS is bypassed rather than silently returning zero
    # rows — the WHERE clause is the enforcement mechanism here.
    database_url_analytics: str = "postgresql://d2c:d2c@localhost:5434/d2c"

    openai_api_key: str = ""

    shopify_shop_domain: str = ""
    shopify_access_token: str = ""

    meta_access_token: str = ""
    meta_ad_account_id: str = ""

    shiprocket_token: str = ""

    # API authentication — format: "key1:merchant1,key2:merchant2"
    api_keys_raw: str = ""
    # CORS allowed origins
    allowed_origins: list[str] = ["http://localhost:10002"]
    # Set true in .env to allow keyless access (defaults to merchant "demo"); never in prod
    dev_mode: bool = False

    # Margin Watch agent thresholds
    rto_unit_cost_inr: float = 150.0
    roas_alert_threshold: float = 2.0
    adset_pause_cut_fraction: float = 0.30
    min_shipments_for_courier_signal: int = 5

    @property
    def api_key_map(self) -> dict[str, str]:
        """Parse api_keys_raw into {api_key: merchant_id}."""
        if not self.api_keys_raw:
            return {}
        result: dict[str, str] = {}
        for pair in self.api_keys_raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                key, merchant = pair.split(":", 1)
                key = key.strip()
                merchant = merchant.strip()
                if key and merchant:
                    result[key] = merchant
        return result


settings = Settings()
