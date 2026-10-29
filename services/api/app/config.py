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
    # Where Stripe Checkout redirects the buyer back to (routers/payments.py) --
    # the frontend's own base origin, not this API's. Optional like the two
    # settings below: a deployment that never configures Stripe just gets a 501
    # from the checkout/webhook endpoints instead of failing to boot.
    frontend_url: str = "http://localhost:3000"
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    # The platform's cut of a paid listing's sale price, in basis points (1500 = 15.00%) --
    # passed to Stripe as a flat application_fee_amount computed from this at checkout time
    # (routers/payments.py), not a Stripe-side percentage parameter (Stripe's own API takes
    # a flat cents amount, not a rate). A deliberate product/business number, not something
    # to change casually — see routers/payments.py's own docstring for the reasoning.
    platform_fee_bps: int = 1500

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    class Config:
        env_file = ".env"


settings = Settings()
