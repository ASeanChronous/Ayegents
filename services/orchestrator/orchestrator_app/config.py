from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    anthropic_api_key: str
    encryption_key: str
    e2b_api_key: str | None = None  # only required if any agent uses a code_exec tool
    poll_interval_seconds: float = 2.0
    worker_id: str = "orchestrator-1"

    class Config:
        env_file = ".env"


settings = Settings()
