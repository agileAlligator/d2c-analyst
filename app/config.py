from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://d2c:d2c@localhost:5432/d2c"
    
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
