from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    anthropic_api_key: str
    encryption_key: str
    supabase_url: str  # required: every request is authenticated against this project (app/auth.py)
    supabase_anon_key: str  # the 'apikey' header GET /auth/v1/user expects, per Supabase's own convention
    supabase_service_role_key: str | None = None
    environment: str = "development"
    # Comma-separated list — the frontend origin(s) allowed to call this API from
    # a browser (CORSMiddleware, main.py). Defaults to the local Next.js dev
    # server; set to the real deployed frontend origin(s) in production.
    cors_allowed_origins: str = "http://localhost:3000"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    class Config:
        env_file = ".env"


settings = Settings()
