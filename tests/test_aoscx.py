"""Tests for AOS-CX integration."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from centralmind.auth import AoscxAuth
from centralmind.config import ServerConfig
from centralmind.server import CentralMindServer


@pytest.fixture
def mock_spec_file():
    """Create a mock OpenAPI spec file for testing."""
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Test AOS-CX API", "version": "1.0.0"},
        "paths": {
            "/system": {
                "get": {
                    "summary": "Get system info",
                    "operationId": "getSystem",
                }
            },
        },
    }
    
    tmp_path = Path(tempfile.gettempdir()) / "test_aoscx_spec.json"
    with open(tmp_path, "w") as f:
        json.dump(spec, f)
    
    yield str(tmp_path)
    tmp_path.unlink(missing_ok=True)


@pytest.fixture
def deno_path():
    """Get path to Deno binary."""
    import shutil
    deno_in_path = shutil.which("deno")
    if deno_in_path:
        return deno_in_path
    
    home = Path.home()
    deno_in_home = home / ".deno" / "bin" / "deno"
    if deno_in_home.exists():
        return str(deno_in_home)
    
    pytest.skip("Deno not found")


def test_aoscx_auth_login():
    """Test AOS-CX login and token extraction."""
    auth = AoscxAuth(username="admin", password="password", verify_ssl=False)
    
    with patch("httpx.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"X-Csrf-Token": "mock-csrf-token"}
        mock_response.cookies = {"sessionId": "testcookie"}
        mock_post.return_value = mock_response
        
        token = auth.get_token("10.1.1.1", "v10.13")
        
        assert token == "sessionId=testcookie|||mock-csrf-token"
        mock_post.assert_called_once()
        
        # Verify call arguments
        args, kwargs = mock_post.call_args
        assert args[0] == "https://10.1.1.1/rest/v10.13/login"
        assert kwargs["params"]["username"] == "admin"
        assert kwargs["params"]["password"] == "password"
        assert kwargs["headers"]["x-use-csrf-token"] == "true"


def test_aoscx_auth_cache():
    """Test AOS-CX token caching per switch."""
    auth = AoscxAuth(username="admin", password="password", verify_ssl=False)
    
    with patch("httpx.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"X-Csrf-Token": "token1"}
        mock_response.cookies = {"sessionId": "cookie1"}
        mock_post.return_value = mock_response
        
        # First call to switch 1
        t1 = auth.get_token("10.1.1.1", "v10.13")
        assert t1 == "sessionId=cookie1|||token1"
        assert mock_post.call_count == 1
        
        # Second call to switch 1 (should be cached)
        t1_cached = auth.get_token("10.1.1.1", "v10.13")
        assert t1_cached == "sessionId=cookie1|||token1"
        assert mock_post.call_count == 1
        
        # Call to switch 2
        mock_response.headers = {"X-Csrf-Token": "token2"}
        mock_response.cookies = {"sessionId": "cookie2"}
        t2 = auth.get_token("10.2.2.2", "v10.13")
        assert t2 == "sessionId=cookie2|||token2"
        assert mock_post.call_count == 2


@pytest.mark.asyncio
async def test_aoscx_server_integration(mock_spec_file, deno_path):
    """Test AOS-CX tool registration and execution flow in the server."""
    config = ServerConfig(
        aoscx_username="admin",
        aoscx_password="password",
        deno_path=deno_path
    )
    
    mock_auth = MagicMock(spec=AoscxAuth)
    mock_auth.get_token.return_value = "fake-csrf"
    mock_auth.host = "*"
    
    server = CentralMindServer(
        config=config,
        aoscx_auth=mock_auth,
        aoscx_spec_path=mock_spec_file
    )
    
    import mcp.types
    req = mcp.types.ListToolsRequest(method="tools/list")
    resp = await server.server.request_handlers[mcp.types.ListToolsRequest](req)
    tools = resp.root.tools
    
    execute_tool = next(t for t in tools if t.name == "execute_aoscx")
    
    properties = execute_tool.inputSchema["properties"]
    assert "switch_ip" in properties
    assert "version" in properties
    assert "code" in properties
    assert "switch_ip" in execute_tool.inputSchema["required"]
    
    # 2. Verify Execution passes dynamic params to sandbox
    with patch.object(server.platforms["aoscx"]["sandbox"], "run_execute", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = {"status": "success"}
        
        result = await server._handle_execute("aoscx", {
            "code": "async () => ({})",
            "switch_ip": "10.1.1.1",
            "version": "v10.13"
        })
        
        assert "success" in result[0].text
        
        # Verify sandbox was called with correct overrides
        mock_run.assert_called_once()
        kwargs = mock_run.call_args[1]
        assert kwargs["api_host"] == "10.1.1.1"
        assert kwargs["base_url"] == "https://10.1.1.1/rest/v10.13"
        assert kwargs["api_token"] == "fake-csrf"