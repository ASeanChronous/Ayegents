from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    anthropic_api_key: str
    encryption_key: str
    supabase_url: str  # required: every request is authenticated against this project (app/auth.py)
    supabase_anon_key: str  # the 'apikey' header GET /auth/v1/user expects, per Supabase's own convention
    supabase_service_role_key: str | None = None
    environment: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()
