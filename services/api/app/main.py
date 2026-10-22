from fastapi import FastAPI

from app.db import close_pool
from app.routers import agent_versions, agents, listings, runs, tools, workspaces

app = FastAPI(title="Agent Ecosystem API")

app.include_router(workspaces.router)
app.include_router(agents.router)
app.include_router(agent_versions.router)
app.include_router(tools.router)
app.include_router(runs.router)
app.include_router(listings.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("shutdown")
async def shutdown():
    await close_pool()
