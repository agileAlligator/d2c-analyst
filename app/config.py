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


settings = Settings()
