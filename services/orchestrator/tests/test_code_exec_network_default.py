"""Regression suite for §18 (docs/01-architecture.md): tool_gateway.py's
_dispatch_code_exec must explicitly deny all outbound network access for a
code_exec tool with no allowed_hosts configured (the common case -- it's an
optional, opt-in field) -- omitting E2B's `network` kwarg entirely is NOT
the same as "no network by default"; E2B's own SDK docs it as "all outbound
traffic is allowed" when unspecified.

No real E2B sandbox is reachable in this dev environment (no E2B_API_KEY
configured), and this project has never had a live E2B account to test
against -- same mocking boundary this project already uses for the
genuinely external/uncontrollable (the Anthropic client). Stubs
e2b_code_interpreter.AsyncSandbox.create to capture exactly the kwargs
_dispatch_code_exec passes, and calls the real, unmodified function.
"""

from types import SimpleNamespace

import pytest

from orchestrator_app import tool_gateway


class _FakeExecution:
    error = None
    logs = SimpleNamespace(stdout=[], stderr=[])
    results = []


class _FakeSandbox:
    def __init__(self):
        pass

    async def run_code(self, code, timeout):
        return _FakeExecution()

    async def kill(self):
        pass


def _patch_sandbox_create(monkeypatch):
    captured = {}

    class _FakeAsyncSandbox:
        @staticmethod
        async def create(**kwargs):
            captured.update(kwargs)
            return _FakeSandbox()

    monkeypatch.setattr("e2b_code_interpreter.AsyncSandbox", _FakeAsyncSandbox)
    return captured


@pytest.mark.asyncio
async def test_denies_all_egress_by_default(monkeypatch):
    monkeypatch.setattr(tool_gateway.settings, "e2b_api_key", "fake-test-key")
    captured = _patch_sandbox_create(monkeypatch)

    tool = {"allowed_hosts": None}
    await tool_gateway._dispatch_code_exec(tool, {}, {"code": "1 + 1"})

    assert "network" in captured
    deny_out = captured["network"]["deny_out"]
    # E2B's own documented way to block all egress -- the catch-all CIDR its docs use.
    assert deny_out(SimpleNamespace(all_traffic="0.0.0.0/0")) == ["0.0.0.0/0"]


@pytest.mark.asyncio
async def test_still_allows_configured_hosts(monkeypatch):
    monkeypatch.setattr(tool_gateway.settings, "e2b_api_key", "fake-test-key")
    captured = _patch_sandbox_create(monkeypatch)

    tool = {"allowed_hosts": ["api.example.com"]}
    await tool_gateway._dispatch_code_exec(tool, {}, {"code": "1 + 1"})

    assert captured["network"] == {"allow_out": ["api.example.com"]}


@pytest.mark.asyncio
async def test_without_api_key_fails_before_touching_the_sandbox(monkeypatch):
    # Confirms the two tests above actually exercise _dispatch_code_exec's own
    # network-kwarg logic rather than passing vacuously -- without a configured
    # key, the function must raise before ever reaching AsyncSandbox.create.
    monkeypatch.setattr(tool_gateway.settings, "e2b_api_key", None)

    with pytest.raises(tool_gateway.ToolDispatchError, match="E2B_API_KEY"):
        await tool_gateway._dispatch_code_exec({"allowed_hosts": None}, {}, {"code": "1 + 1"})
