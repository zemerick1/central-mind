"""Tests for the MCP server implementation."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from centralmind.clients_store import ClientsStore
from centralmind.config import ServerConfig
from centralmind.server import CentralMindServer


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_spec_file():
    """Create a mock OpenAPI spec file for testing."""
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "{baseUrl}"}],
        "paths": {
            "/api/v1/self": {
                "get": {
                    "summary": "Get self",
                    "tags": ["user"],
                    "operationId": "getSelf",
                }
            },
            "/api/v1/orgs/{org_id}/sites": {
                "get": {
                    "summary": "List sites",
                    "tags": ["sites"],
                    "operationId": "listSites",
                }
            },
        },
    }

    tmp_path = Path(tempfile.gettempdir()) / f"test_spec_{id(spec)}.json"
    with open(tmp_path, "w") as f:
        json.dump(spec, f)
        f.flush()

    yield str(tmp_path)

    tmp_path.unlink(missing_ok=True)


@pytest.fixture
def deno_path():
    """Get path to Deno binary."""
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
def mock_auth():
    """Create a mock CentralAuth instance (avoids real network calls)."""
    auth = MagicMock()
    auth.host = "internal.api.central.arubanetworks.com"
    auth.base_url = "https://internal.api.central.arubanetworks.com"
    auth.get_token.return_value = "test-bearer-token-12345"
    return auth


@pytest.fixture
def server_config(deno_path):
    """Create a test server config (global settings only, no credentials)."""
    return ServerConfig(deno_path=deno_path)


@pytest.fixture
def clients_store(tmp_path):
    """Create an isolated ClientsStore with one 'default' client configured for Central."""
    store = ClientsStore(path=tmp_path / "clients.json", key_path=tmp_path / "secret.key")
    store.create(
        name="default",
        central_client_id="test-client-id",
        central_client_secret="test-client-secret",
        central_base_url="https://internal.api.central.arubanetworks.com",
    )
    return store


@pytest.fixture
def server(server_config, clients_store, mock_spec_file, mock_auth, monkeypatch):
    """Create a CentralMindServer instance for testing, with real Central auth
    replaced by a mock so tests never hit the network."""
    monkeypatch.setattr("centralmind.server.build_platform_auth", lambda platform, profile: mock_auth)
    return CentralMindServer(
        config=server_config,
        clients_store=clients_store,
        resolved_spec_paths={"central": mock_spec_file},
    )


# =============================================================================
# Tool Listing Tests
# =============================================================================

class TestToolListing:
    """Tests for MCP tool listing."""

    @pytest.mark.asyncio
    async def test_handlers_exist(self, server):
        assert server.server is not None
        assert hasattr(server, '_handle_search')
        assert hasattr(server, '_handle_execute')

    @pytest.mark.asyncio
    async def test_search_handler_exists(self, server):
        assert callable(server._handle_search)

    @pytest.mark.asyncio
    async def test_execute_handler_exists(self, server):
        assert callable(server._handle_execute)


# =============================================================================
# Search Handler Tests
# =============================================================================

class TestSearchHandler:
    """Tests for the search tool handler."""

    @pytest.mark.asyncio
    async def test_search_with_valid_code(self, server):
        result = await server._handle_search("central", {
            "code": "async () => { return Object.keys(spec.paths).length; }"
        })

        assert len(result) == 1
        result_data = json.loads(result[0].text)
        assert result_data == 2  # 2 paths in mock spec

    @pytest.mark.asyncio
    async def test_search_with_missing_code(self, server):
        result = await server._handle_search("central", {})

        assert len(result) == 1
        assert "Error" in result[0].text
        assert "'code' parameter required" in result[0].text

    @pytest.mark.asyncio
    async def test_search_returns_filtered_results(self, server):
        result = await server._handle_search("central", {
            "code": """async () => {
                const results = [];
                for (const [path, methods] of Object.entries(spec.paths)) {
                    for (const [method, op] of Object.entries(methods)) {
                        if (op.tags?.includes('sites')) {
                            results.push({path, method, summary: op.summary});
                        }
                    }
                }
                return results;
            }"""
        })

        assert len(result) == 1
        result_data = json.loads(result[0].text)
        assert len(result_data) == 1
        assert result_data[0]["path"] == "/api/v1/orgs/{org_id}/sites"


# =============================================================================
# Execute Handler Tests
# =============================================================================

class TestExecuteHandler:
    """Tests for the execute tool handler."""

    @pytest.mark.asyncio
    async def test_execute_with_valid_code(self, server):
        result = await server._handle_execute("central", {
            "code": """async () => {
                return {test: true, timestamp: Date.now()};
            }"""
        })

        assert len(result) == 1
        result_data = json.loads(result[0].text)
        assert result_data.get("test") == True
        assert "timestamp" in result_data

    @pytest.mark.asyncio
    async def test_execute_with_missing_code(self, server):
        result = await server._handle_execute("central", {})

        assert len(result) == 1
        assert "Error" in result[0].text
        assert "'code' parameter required" in result[0].text

    @pytest.mark.asyncio
    async def test_execute_can_access_central_object(self, server):
        result = await server._handle_execute("central", {
            "code": """async () => {
                return {
                    hasCentral: typeof central !== 'undefined',
                    hasRequest: typeof central.request === 'function',
                    allowedMethods: central.allowedMethods
                };
            }"""
        })

        assert len(result) == 1
        result_data = json.loads(result[0].text)
        assert result_data["hasCentral"] == True
        assert result_data["hasRequest"] == True
        assert "GET" in result_data["allowedMethods"]


# =============================================================================
# Unknown Tool Tests
# =============================================================================

class TestUnknownTool:
    """Tests for unknown tool handling."""

    @pytest.mark.asyncio
    async def test_search_on_unconfigured_platform_errors(self, server):
        with pytest.raises((ValueError, KeyError)):
            await server._handle_search("nonexistent-platform", {"code": "async () => 1;"})


# =============================================================================
# Exception Scrubbing Tests
# =============================================================================

class TestExceptionScrubbing:
    """Tests for token scrubbing in exception messages."""

    @pytest.mark.asyncio
    async def test_exception_scrubs_token_from_execute_result(self, server):
        secret_token = "super-secret-token-xyz789"
        # Force the cached bundle's auth to return our secret so run_execute's
        # own scrubbing (via token_to_scrub) redacts it from the Deno error.
        bundle = server._get_bundle(server._get_active_client_id(), "central")
        bundle["auth"].get_token.return_value = secret_token

        result = await server._handle_execute("central", {
            "code": f"""async () => {{
                throw new Error("Token leak: {secret_token}");
            }}"""
        })

        assert len(result) == 1
        result_text = result[0].text
        assert secret_token not in result_text
        assert "[REDACTED]" in result_text


# =============================================================================
# Server Initialization / Bundle Caching Tests
# =============================================================================

class TestServerInitialization:
    """Tests for server initialization and lazy bundle construction."""

    def test_server_initializes_without_eager_auth(self, server_config, clients_store, mock_spec_file):
        """Server should construct without authenticating anything up front —
        auth/sandbox are built lazily on first tool call."""
        server = CentralMindServer(
            config=server_config,
            clients_store=clients_store,
            resolved_spec_paths={"central": mock_spec_file},
        )
        assert server._bundle_cache == {}

    @pytest.mark.asyncio
    async def test_bundle_is_cached_after_first_use(self, server):
        await server._handle_search("central", {"code": "async () => 1;"})
        active_id = server._get_active_client_id()
        # Cache key includes the profile's updated_at, so match on prefix
        # rather than the exact tuple.
        assert any(key[0] == active_id and key[1] == "central" for key in server._bundle_cache)

    def test_server_fails_with_missing_spec_on_first_use(self, server_config, clients_store, mock_auth, monkeypatch):
        monkeypatch.setattr("centralmind.server.build_platform_auth", lambda platform, profile: mock_auth)
        server = CentralMindServer(
            config=server_config,
            clients_store=clients_store,
            resolved_spec_paths={"central": "/nonexistent/path/spec.json"},
        )
        with pytest.raises(FileNotFoundError):
            server._get_spec_index("central")

    def test_server_inherits_api_mode(self, clients_store, mock_spec_file, mock_auth, monkeypatch, deno_path):
        monkeypatch.setattr("centralmind.server.build_platform_auth", lambda platform, profile: mock_auth)
        config = ServerConfig(deno_path=deno_path, centralmind_api_mode="readwrite")
        server = CentralMindServer(
            config=config,
            clients_store=clients_store,
            resolved_spec_paths={"central": mock_spec_file},
        )
        bundle = server._get_bundle(server._get_active_client_id(), "central")
        assert bundle["sandbox"].api_mode == "readwrite"
        assert "POST" in bundle["sandbox"].allowed_methods

    def test_client_api_mode_override_is_respected(self, clients_store, mock_spec_file, mock_auth, monkeypatch, deno_path):
        """A client's own api_mode narrows access when the server allows more."""
        config = ServerConfig(deno_path=deno_path, centralmind_api_mode="all")
        profile = clients_store.list()[0]
        profile.api_mode = "readonly"
        clients_store.save(profile)
        monkeypatch.setattr("centralmind.server.build_platform_auth", lambda platform, profile: mock_auth)
        server = CentralMindServer(
            config=config,
            clients_store=clients_store,
            resolved_spec_paths={"central": mock_spec_file},
        )
        bundle = server._get_bundle(profile.id, "central")
        assert bundle["sandbox"].api_mode == "readonly"

    def test_client_api_mode_override_cannot_exceed_server_ceiling(
        self, clients_store, mock_spec_file, mock_auth, monkeypatch, deno_path
    ):
        """A client asking for more access than the server allows gets capped, not honored."""
        config = ServerConfig(deno_path=deno_path, centralmind_api_mode="readonly")
        profile = clients_store.list()[0]
        profile.api_mode = "all"  # attempt to grant itself more than the server ceiling
        clients_store.save(profile)
        monkeypatch.setattr("centralmind.server.build_platform_auth", lambda platform, profile: mock_auth)
        server = CentralMindServer(
            config=config,
            clients_store=clients_store,
            resolved_spec_paths={"central": mock_spec_file},
        )
        bundle = server._get_bundle(profile.id, "central")
        assert bundle["sandbox"].api_mode == "readonly"
        assert bundle["sandbox"].allowed_methods == ["GET"]

    def test_editing_client_api_mode_invalidates_cached_bundle(
        self, clients_store, mock_spec_file, mock_auth, monkeypatch, deno_path
    ):
        """Changing a client's api_mode while the server is running should take
        effect on the next call, not require a restart."""
        config = ServerConfig(deno_path=deno_path, centralmind_api_mode="all")
        monkeypatch.setattr("centralmind.server.build_platform_auth", lambda platform, profile: mock_auth)
        server = CentralMindServer(
            config=config,
            clients_store=clients_store,
            resolved_spec_paths={"central": mock_spec_file},
        )
        client_id = server._get_active_client_id()

        first_bundle = server._get_bundle(client_id, "central")
        assert first_bundle["sandbox"].api_mode == "all"

        profile = clients_store.get(client_id)
        profile.api_mode = "readonly"
        clients_store.save(profile)

        second_bundle = server._get_bundle(client_id, "central")
        assert second_bundle["sandbox"].api_mode == "readonly"
        assert second_bundle is not first_bundle


# =============================================================================
# Config Spec Path Tests
# =============================================================================

class TestConfigSpecPath:
    """Tests for the spec_path configuration option."""

    def test_config_accepts_spec_path(self, deno_path):
        config = ServerConfig(
            deno_path=deno_path,
            centralmind_spec_path="/custom/path/spec.json",
        )
        assert config.centralmind_spec_path == "/custom/path/spec.json"

    def test_config_spec_path_defaults_to_none(self, deno_path):
        config = ServerConfig(deno_path=deno_path)
        assert config.centralmind_spec_path is None
