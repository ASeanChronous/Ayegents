from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import close_pool
from app.routers import agent_versions, agents, flows, listings, marketplace, payments, runs, tools, triggers, webhooks, workspaces

app = FastAPI(title="Agent Ecosystem API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workspaces.router)
app.include_router(agents.router)
app.include_router(agent_versions.router)
app.include_router(flows.router)
app.include_router(triggers.router)
app.include_router(tools.router)
app.include_router(runs.router)
app.include_router(listings.router)
app.include_router(marketplace.listings_router)
app.include_router(marketplace.install_router)
app.include_router(payments.checkout_router)
# Public, unauthenticated -- see webhooks.py's/payments.py's own docstrings.
# Order matters here: FastAPI matches routes in registration order, and
# webhooks.router's POST /webhooks/{trigger_id} is a path parameter that
# would otherwise greedily match "stripe" as a trigger_id and shadow the
# literal POST /webhooks/stripe below it — registering the literal path
# first is what makes it win.
app.include_router(payments.stripe_webhook_router)
app.include_router(webhooks.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("shutdown")
async def shutdown():
    await close_pool()
