from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    anthropic_api_key: str
    encryption_key: str
    e2b_api_key: str | None = None  # only required if any agent uses a code_exec tool
    poll_interval_seconds: float = 2.0
    # Separate from poll_interval_seconds -- a schedule trigger's own
    # granularity is minutes (cron's finest unit), so checking every 2s
    # like the run queue does is pure waste. 30s keeps a trigger's actual
    # fire time within half a minute of its cron schedule, which is as
    # precise as cron itself claims to be.
    schedule_poll_interval_seconds: float = 30.0
    worker_id: str = "orchestrator-1"

    class Config:
        env_file = ".env"


settings = Settings()
