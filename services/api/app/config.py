from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    anthropic_api_key: str
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    environment: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()
