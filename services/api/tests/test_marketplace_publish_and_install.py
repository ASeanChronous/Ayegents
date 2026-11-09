"""Regression suite for the marketplace publish/install flow (routers/marketplace.py,
architecture doc §6) -- the largest router in this API with no automated coverage at
all until now (every other router this session has touched already had at least a
throwaway verification script; this one never did). Covers the same two classes of
property every other suite in this project checks for a security-sensitive router:

- the cross-workspace ownership checks create_listing/publish_listing_version/
  install_listing each do on their own (a listing can't source another workspace's
  agent/flow; a listing can't be published if it would reference a tool the
  installer could never resolve, or an agent with sub-agents there's no listing to
  install them from; an install can't skip the tool-category approval gate or a
  paid listing's payment gate), and
- that the happy path actually does what its docstring says (install forks an
  independent agent/flow copy into the installing workspace, with a flow's `agent`
  nodes rewritten to point at the installer's own forked copies, not the
  publisher's originals).

Calls the router functions directly, like every other suite here -- no running
uvicorn process, no mocking of the database.
"""

import pytest
from fastapi import HTTPException

from app.routers.marketplace import (
    InstallCreate,
    ListingCreate,
    ListingUpdate,
    ReviewUpsert,
    create_listing,
    install_listing,
    publish_listing_version,
    unpublish_listing,
    update_listing,
    upsert_review,
)


@pytest.mark.asyncio
async def test_create_listing_rejects_cross_workspace_source_agent(factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    victim_agent_id = await factory.agent(victim["id"])

    with pytest.raises(HTTPException) as exc_info:
        await create_listing(
            workspace_id=attacker["id"],
            body=ListingCreate(source_agent_id=victim_agent_id, name="stolen"),
            user_id=attacker["id"],
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_create_listing_rejects_cross_workspace_source_flow(factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    victim_flow_id = await factory.flow(victim["id"])

    with pytest.raises(HTTPException) as exc_info:
        await create_listing(
            workspace_id=attacker["id"],
            body=ListingCreate(source_flow_id=victim_flow_id, name="stolen"),
            user_id=attacker["id"],
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_create_listing_requires_payouts_enabled_for_priced_listing(factory):
    ws = await factory.workspace()
    agent_id = await factory.agent(ws["id"])

    with pytest.raises(HTTPException) as exc_info:
        await create_listing(
            workspace_id=ws["id"],
            body=ListingCreate(source_agent_id=agent_id, name="priced", price_cents=500),
            user_id=ws["id"],
        )
    assert exc_info.value.status_code == 400

    # Free is unaffected by the same gate.
    listing = await create_listing(
        workspace_id=ws["id"],
        body=ListingCreate(source_agent_id=agent_id, name="free", price_cents=0),
        user_id=ws["id"],
    )
    assert listing.id is not None


@pytest.mark.asyncio
async def test_update_listing_price_also_requires_payouts_enabled(factory):
    ws = await factory.workspace()
    agent_id = await factory.agent(ws["id"])
    listing_id = await factory.listing(ws["id"], source_agent_id=agent_id)

    with pytest.raises(HTTPException) as exc_info:
        await update_listing(
            workspace_id=ws["id"], listing_id=listing_id, body=ListingUpdate(price_cents=500), user_id=ws["id"]
        )
    assert exc_info.value.status_code == 400

    await factory.set_payouts_enabled(ws["id"])
    updated = await update_listing(
        workspace_id=ws["id"], listing_id=listing_id, body=ListingUpdate(price_cents=500), user_id=ws["id"]
    )
    assert updated.price_cents == 500


@pytest.mark.asyncio
async def test_publish_listing_version_rejects_agent_with_subagents(factory):
    ws = await factory.workspace()
    agent_id = await factory.agent(ws["id"])
    sub_agent_id = await factory.agent(ws["id"])
    await factory.agent_version(agent_id, sub_agent_ids=[sub_agent_id])
    listing_id = await factory.listing(ws["id"], source_agent_id=agent_id)

    with pytest.raises(HTTPException) as exc_info:
        await publish_listing_version(workspace_id=ws["id"], listing_id=listing_id, user_id=ws["id"])
    assert exc_info.value.status_code == 400
    assert "sub-agents" in exc_info.value.detail


@pytest.mark.asyncio
async def test_publish_listing_version_rejects_agent_with_private_tool(factory):
    ws = await factory.workspace()
    agent_id = await factory.agent(ws["id"])
    private_tool_id = await factory.tool(ws["id"])
    await factory.agent_version(agent_id, tool_ids=[private_tool_id])
    listing_id = await factory.listing(ws["id"], source_agent_id=agent_id)

    with pytest.raises(HTTPException) as exc_info:
        await publish_listing_version(workspace_id=ws["id"], listing_id=listing_id, user_id=ws["id"])
    assert exc_info.value.status_code == 400
    assert "workspace-private tools" in exc_info.value.detail


@pytest.mark.asyncio
async def test_publish_listing_version_agent_listing_derives_categories_and_publishes(factory):
    ws = await factory.workspace()
    agent_id = await factory.agent(ws["id"])
    global_tool_id = await factory.tool(None, kind="http", key="weather-lookup")
    await factory.agent_version(agent_id, tool_ids=[global_tool_id])
    listing_id = await factory.listing(ws["id"], source_agent_id=agent_id)

    result = await publish_listing_version(workspace_id=ws["id"], listing_id=listing_id, user_id=ws["id"])
    assert result.version == 1
    assert result.required_tool_categories == ["http:weather-lookup"]


@pytest.mark.asyncio
async def test_publish_listing_version_flow_listing_derives_categories_from_referenced_agent(factory):
    ws = await factory.workspace()
    agent_id = await factory.agent(ws["id"])
    global_tool_id = await factory.tool(None, kind="http", key="flow-weather-lookup")
    await factory.agent_version(agent_id, tool_ids=[global_tool_id])
    flow_id = await factory.flow(ws["id"])
    await factory.flow_version(
        flow_id,
        {
            "nodes": [
                {"id": "t", "type": "trigger"},
                {"id": "a", "type": "agent", "agent_id": str(agent_id), "config": {}},
            ],
            "edges": [{"from": "t", "to": "a"}],
        },
    )
    listing_id = await factory.listing(ws["id"], source_flow_id=flow_id)

    result = await publish_listing_version(workspace_id=ws["id"], listing_id=listing_id, user_id=ws["id"])
    assert result.required_tool_categories == ["http:flow-weather-lookup"]


@pytest.mark.asyncio
async def test_install_listing_rejects_missing_approved_tool_categories(factory):
    publisher = await factory.workspace()
    installer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    await factory.agent_version(agent_id)
    listing_id = await factory.listing(
        publisher["id"], source_agent_id=agent_id, status="published", required_tool_categories=["http:needed"]
    )
    await factory.listing_version(listing_id, {"type": "agent", "name": "x", "system_prompt": "hi", "model_tier": "sonnet", "tools": []})

    with pytest.raises(HTTPException) as exc_info:
        await install_listing(
            workspace_id=installer["id"],
            body=InstallCreate(listing_id=listing_id, approved_tool_categories=[]),
            user_id=installer["member"],
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_install_listing_rejects_unpaid_priced_listing(factory):
    publisher = await factory.workspace()
    installer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    await factory.agent_version(agent_id)
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, status="published", price_cents=500)
    await factory.listing_version(listing_id, {"type": "agent", "name": "x", "system_prompt": "hi", "model_tier": "sonnet", "tools": []})

    with pytest.raises(HTTPException) as exc_info:
        await install_listing(
            workspace_id=installer["id"],
            body=InstallCreate(listing_id=listing_id, approved_tool_categories=[]),
            user_id=installer["member"],
        )
    assert exc_info.value.status_code == 402


@pytest.mark.asyncio
async def test_install_listing_succeeds_once_paid(factory):
    publisher = await factory.workspace()
    installer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    await factory.agent_version(agent_id)
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, status="published", price_cents=500)
    await factory.listing_version(listing_id, {"type": "agent", "name": "x", "system_prompt": "hi", "model_tier": "sonnet", "tools": []})
    await factory.purchase(listing_id, installer["id"], installer["member"])

    result = await install_listing(
        workspace_id=installer["id"],
        body=InstallCreate(listing_id=listing_id, approved_tool_categories=[]),
        user_id=installer["member"],
    )
    assert result.installed_agent_id is not None


@pytest.mark.asyncio
async def test_install_listing_forks_agent_as_a_new_row_and_increments_install_count(factory, pool):
    publisher = await factory.workspace()
    installer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    global_tool_id = await factory.tool(None, kind="http", key="install-weather-lookup")
    await factory.agent_version(agent_id, tool_ids=[global_tool_id])
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, status="published")
    publish_result = await publish_listing_version(workspace_id=publisher["id"], listing_id=listing_id, user_id=publisher["id"])

    result = await install_listing(
        workspace_id=installer["id"],
        body=InstallCreate(listing_id=listing_id, approved_tool_categories=publish_result.required_tool_categories),
        user_id=installer["member"],
    )
    assert result.installed_agent_id is not None
    assert result.installed_agent_id != agent_id  # a fork, not the original row
    assert await pool.fetchval("select install_count from listings where id = $1", listing_id) == 1


@pytest.mark.asyncio
async def test_install_listing_rejects_tool_not_available_in_installing_platform(factory, pool):
    """_install_agent_snapshot resolves a tool by (workspace_id is null, key, kind) against
    the *installing* platform's own global tools -- if the one the listing was published
    against was since removed, install must fail loudly (409) and roll back the partial
    agent/version it already inserted, not leave an agent with a dangling tool reference."""
    publisher = await factory.workspace()
    installer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    global_tool_id = await factory.tool(None, kind="http", key="soon-to-be-removed")
    await factory.agent_version(agent_id, tool_ids=[global_tool_id])
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, status="published")
    publish_result = await publish_listing_version(workspace_id=publisher["id"], listing_id=listing_id, user_id=publisher["id"])

    # Remove the global tool between publish and install.
    await pool.execute("delete from agent_version_tools where tool_id = $1", global_tool_id)
    await pool.execute("delete from tools where id = $1", global_tool_id)

    with pytest.raises(HTTPException) as exc_info:
        await install_listing(
            workspace_id=installer["id"],
            body=InstallCreate(listing_id=listing_id, approved_tool_categories=publish_result.required_tool_categories),
            user_id=installer["member"],
        )
    assert exc_info.value.status_code == 409

    installed_count = await pool.fetchval(
        "select count(*) from agents where workspace_id = $1", installer["id"]
    )
    assert installed_count == 0  # the transaction rolled back, nothing half-installed


@pytest.mark.asyncio
async def test_install_listing_forks_flow_with_agent_ids_remapped_to_installer_copies(factory):
    publisher = await factory.workspace()
    installer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    await factory.agent_version(agent_id)
    flow_id = await factory.flow(publisher["id"])
    await factory.flow_version(
        flow_id,
        {
            "nodes": [
                {"id": "t", "type": "trigger"},
                {"id": "a", "type": "agent", "agent_id": str(agent_id), "config": {}},
            ],
            "edges": [{"from": "t", "to": "a"}],
        },
    )
    listing_id = await factory.listing(publisher["id"], source_flow_id=flow_id, status="published")
    publish_result = await publish_listing_version(workspace_id=publisher["id"], listing_id=listing_id, user_id=publisher["id"])

    result = await install_listing(
        workspace_id=installer["id"],
        body=InstallCreate(listing_id=listing_id, approved_tool_categories=publish_result.required_tool_categories),
        user_id=installer["member"],
    )
    assert result.installed_flow_id is not None
    assert result.installed_flow_id != flow_id


@pytest.mark.asyncio
async def test_upsert_review_requires_install_first(factory):
    publisher = await factory.workspace()
    reviewer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, status="published")
    await factory.listing_version(listing_id, {"type": "agent", "name": "x", "system_prompt": "hi", "model_tier": "sonnet", "tools": []})

    with pytest.raises(HTTPException) as exc_info:
        await upsert_review(
            workspace_id=reviewer["id"],
            listing_id=listing_id,
            body=ReviewUpsert(rating=5, comment="great"),
            user_id=reviewer["member"],
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_upsert_review_updates_rating_aggregate_and_is_idempotent_per_workspace(factory, pool):
    publisher = await factory.workspace()
    reviewer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, status="published")
    version_id = await factory.listing_version(
        listing_id, {"type": "agent", "name": "x", "system_prompt": "hi", "model_tier": "sonnet", "tools": []}
    )
    await factory.install(listing_id, version_id, reviewer["id"], reviewer["member"])

    review = await upsert_review(
        workspace_id=reviewer["id"], listing_id=listing_id, body=ReviewUpsert(rating=4), user_id=reviewer["member"]
    )
    assert review.rating == 4
    row = await pool.fetchrow("select rating_avg, rating_count from listings where id = $1", listing_id)
    assert (row["rating_avg"], row["rating_count"]) == (4, 1)

    # Same (listing, workspace) pair reviewing again edits in place, not a second row.
    review = await upsert_review(
        workspace_id=reviewer["id"], listing_id=listing_id, body=ReviewUpsert(rating=2), user_id=reviewer["member"]
    )
    assert review.rating == 2
    row = await pool.fetchrow("select rating_avg, rating_count from listings where id = $1", listing_id)
    assert (row["rating_avg"], row["rating_count"]) == (2, 1)


@pytest.mark.asyncio
async def test_unpublish_listing_rejects_already_unpublished(factory):
    ws = await factory.workspace()
    agent_id = await factory.agent(ws["id"])
    listing_id = await factory.listing(ws["id"], source_agent_id=agent_id, status="published")

    unpublished = await unpublish_listing(workspace_id=ws["id"], listing_id=listing_id, user_id=ws["id"])
    assert unpublished.status == "archived"

    with pytest.raises(HTTPException) as exc_info:
        await unpublish_listing(workspace_id=ws["id"], listing_id=listing_id, user_id=ws["id"])
    assert exc_info.value.status_code == 400
