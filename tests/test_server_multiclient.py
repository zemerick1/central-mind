"""Tests for multi-client behavior: list_clients/switch_client tools and
per-session isolation of the "active client" selection."""

import json
from pathlib import Path

import pytest

from centralmind.clients_store import ClientsStore
from centralmind.config import ServerConfig
from centralmind.server import CentralMindServer


@pytest.fixture
def deno_path():
    home = Path.home()
    deno_in_home = home / ".deno" / "bin" / "deno"
    if deno_in_home.exists():
        return str(deno_in_home)
    import shutil
    deno_in_path = shutil.which("deno")
    if deno_in_path:
        return deno_in_path
    pytest.skip("Deno not found")


@pytest.fixture
def server(deno_path, tmp_path):
    config = ServerConfig(deno_path=deno_path)
    clients_store = ClientsStore(path=tmp_path / "clients.json", key_path=tmp_path / "secret.key")
    return CentralMindServer(config=config, clients_store=clients_store, resolved_spec_paths={})


class TestListClients:
    @pytest.mark.asyncio
    async def test_reports_no_clients_when_store_empty(self, server):
        result = await server._handle_list_clients()
        data = json.loads(result[0].text)
        assert data == []

    @pytest.mark.asyncio
    async def test_lists_configured_platforms_and_default(self, server):
        server.clients_store.create(name="Acme", central_client_id="id", central_client_secret="secret")
        result = await server._handle_list_clients()
        data = json.loads(result[0].text)
        assert len(data) == 1
        assert data[0]["name"] == "Acme"
        assert data[0]["platforms"] == ["central"]
        assert data[0]["default"] is True
        assert data[0]["active"] is True  # only client, becomes default => active


class TestSwitchClient:
    @pytest.mark.asyncio
    async def test_requires_client_id(self, server):
        result = await server._handle_switch_client({})
        assert "Error" in result[0].text
        assert "client_id" in result[0].text

    @pytest.mark.asyncio
    async def test_rejects_unknown_client_id(self, server):
        result = await server._handle_switch_client({"client_id": "does-not-exist"})
        assert "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_switches_active_client(self, server):
        server.clients_store.create(name="Client A")
        client_b = server.clients_store.create(name="Client B")

        await server._handle_switch_client({"client_id": client_b.id})

        result = await server._handle_list_clients()
        data = json.loads(result[0].text)
        active = next(c for c in data if c["active"])
        assert active["id"] == client_b.id


class TestSessionIsolation:
    """The whole point of session-scoped active-client tracking: two
    concurrent MCP connections (e.g. two engineers hitting the same
    streamable-http server) must never clobber each other's selection."""

    @pytest.mark.asyncio
    async def test_two_sessions_keep_independent_active_clients(self, server, monkeypatch):
        current_session = {"key": "session-A"}
        monkeypatch.setattr(server, "_session_key", lambda: current_session["key"])

        client_a = server.clients_store.create(name="Client A")
        client_b = server.clients_store.create(name="Client B")

        # Session A explicitly selects client_a
        await server._handle_switch_client({"client_id": client_a.id})

        # Session B explicitly selects client_b
        current_session["key"] = "session-B"
        await server._handle_switch_client({"client_id": client_b.id})

        # Back on session A: should still see client_a active, unaffected by B
        current_session["key"] = "session-A"
        result = await server._handle_list_clients()
        active = next(c for c in json.loads(result[0].text) if c["active"])
        assert active["id"] == client_a.id

        # Session B: should still see client_b active
        current_session["key"] = "session-B"
        result = await server._handle_list_clients()
        active = next(c for c in json.loads(result[0].text) if c["active"])
        assert active["id"] == client_b.id

    @pytest.mark.asyncio
    async def test_new_session_defaults_to_store_default(self, server, monkeypatch):
        current_session = {"key": "session-A"}
        monkeypatch.setattr(server, "_session_key", lambda: current_session["key"])

        client_a = server.clients_store.create(name="Client A")  # becomes default

        current_session["key"] = "brand-new-session"
        result = await server._handle_list_clients()
        active = next(c for c in json.loads(result[0].text) if c["active"])
        assert active["id"] == client_a.id
