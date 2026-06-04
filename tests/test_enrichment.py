"""Unit tests for the Dynamic Enrichment Phase."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import TextContent

from centralmind.config import ServerConfig
from centralmind.server import CentralMindServer


@pytest.fixture
def mock_server():
    """Create a CentralMindServer with dynamic enrichment enabled."""
    config = ServerConfig(
        centralmind_enable_enrichment=True,
        centralmind_max_enrichment_calls=3,
    )
    server = CentralMindServer(config=config)
    # Add a mock platform to platforms dictionary
    server.platforms["central"] = {
        "auth": MagicMock(),
        "sandbox": MagicMock(),
        "spec_path": MagicMock(),
    }
    return server


@pytest.mark.asyncio
async def test_enrichment_appended_when_enabled(mock_server):
    """Verify the enrichment phase correctly appends the _enrichment key when enabled."""
    primary_data = {"status": "ok"}
    primary_result = [TextContent(type="text", text=json.dumps(primary_data))]
    
    response = await mock_server._perform_enrichment("central", primary_result)
    assert len(response) == 1
    
    result_data = json.loads(response[0].text)
    assert "_enrichment" in result_data


@pytest.mark.asyncio
async def test_enrichment_attributes_healthy(mock_server):
    """Verify enrichment attributes are populated correctly in a healthy scenario."""
    healthy_data = {
        "devices": [
            {"name": "AP-01", "status": "online", "online": True},
            {"name": "Switch-01", "status": "connected", "connected": True}
        ]
    }
    primary_result = [TextContent(type="text", text=json.dumps(healthy_data))]
    
    response = await mock_server._perform_enrichment("central", primary_result)
    result_data = json.loads(response[0].text)
    enrichment = result_data["_enrichment"]
    
    assert enrichment["blast_radius"] == "Low"
    assert enrichment["client_impact"]["count"] == 0
    assert "healthy" in enrichment["impact_summary"].lower()
    assert isinstance(enrichment["correlations"], list)
    assert isinstance(enrichment["risks"], list)
    assert isinstance(enrichment["recommendations"], list)
    assert "manual_analysis_prompt" in enrichment
    assert enrichment["manual_analysis_prompt"] == mock_server.manual_analysis_prompt


@pytest.mark.asyncio
async def test_enrichment_attributes_offline_devices(mock_server):
    """Verify enrichment attributes are populated correctly with offline devices."""
    # Scenario: 1 offline device (Medium blast radius, 15 client impact)
    data_1 = {
        "devices": [
            {"name": "AP-01", "status": "offline", "online": False},
            {"name": "Switch-01", "status": "connected", "connected": True}
        ]
    }
    primary_result_1 = [TextContent(type="text", text=json.dumps(data_1))]
    response_1 = await mock_server._perform_enrichment("central", primary_result_1)
    enrichment_1 = json.loads(response_1[0].text)["_enrichment"]
    
    assert enrichment_1["blast_radius"] == "Medium"
    assert enrichment_1["client_impact"]["count"] == 15
    assert "1 offline device" in enrichment_1["impact_summary"]
    assert len(enrichment_1["risks"]) > 0
    assert len(enrichment_1["recommendations"]) > 0
    assert "manual_analysis_prompt" in enrichment_1
    assert enrichment_1["manual_analysis_prompt"] == mock_server.manual_analysis_prompt

    # Scenario: 3 offline devices (High blast radius, 45 client impact)
    data_3 = {
        "devices": [
            {"name": "AP-01", "status": "offline", "online": False},
            {"name": "AP-02", "status": "down", "online": False},
            {"name": "AP-03", "status": "disconnected", "online": False}
        ]
    }
    primary_result_3 = [TextContent(type="text", text=json.dumps(data_3))]
    response_3 = await mock_server._perform_enrichment("central", primary_result_3)
    enrichment_3 = json.loads(response_3[0].text)["_enrichment"]
    
    assert enrichment_3["blast_radius"] == "High"
    assert enrichment_3["client_impact"]["count"] == 45
    assert "3 offline devices" in enrichment_3["impact_summary"]
    assert "manual_analysis_prompt" in enrichment_3
    assert enrichment_3["manual_analysis_prompt"] == mock_server.manual_analysis_prompt

    # Scenario: 6 offline devices (Critical blast radius, 90 client impact)
    data_6 = {
        "devices": [
            {"name": f"AP-{i}", "status": "offline", "online": False}
            for i in range(6)
        ]
    }
    primary_result_6 = [TextContent(type="text", text=json.dumps(data_6))]
    response_6 = await mock_server._perform_enrichment("central", primary_result_6)
    enrichment_6 = json.loads(response_6[0].text)["_enrichment"]
    
    assert enrichment_6["blast_radius"] == "Critical"
    assert enrichment_6["client_impact"]["count"] == 90
    assert "6 offline devices" in enrichment_6["impact_summary"]
    assert "manual_analysis_prompt" in enrichment_6
    assert enrichment_6["manual_analysis_prompt"] == mock_server.manual_analysis_prompt


@pytest.mark.asyncio
async def test_enrichment_attributes_errors(mock_server):
    """Verify enrichment attributes are populated correctly when errors are present."""
    error_data = {
        "error": "unauthorized_client",
        "error_description": "The client credentials are invalid."
    }
    primary_result = [TextContent(type="text", text=json.dumps(error_data))]
    
    response = await mock_server._perform_enrichment("central", primary_result)
    result_data = json.loads(response[0].text)
    enrichment = result_data["_enrichment"]
    
    assert enrichment["blast_radius"] == "Medium"
    assert enrichment["client_impact"]["count"] == 0
    assert any("error" in r.lower() for r in enrichment["risks"])
    assert any("credentials" in r.lower() or "permissions" in r.lower() for r in enrichment["recommendations"])
    assert "manual_analysis_prompt" in enrichment
    assert enrichment["manual_analysis_prompt"] == mock_server.manual_analysis_prompt


@pytest.mark.asyncio
async def test_enrichment_skipped_when_disabled(mock_server):
    """Verify the enrichment phase is skipped and no _enrichment key is appended when disabled."""
    mock_server.config.centralmind_enable_enrichment = False
    
    # Mock self._handle_execute to return an offline device result
    data = {
        "devices": [
            {"name": "AP-01", "status": "offline", "online": False}
        ]
    }
    mock_server._handle_execute = AsyncMock(
        return_value=[TextContent(type="text", text=json.dumps(data))]
    )
    
    # Retrieve the list/call the call_tool handler
    call_tool_handler = mock_server.server._call_tool_handler
    assert call_tool_handler is not None
    
    response = await call_tool_handler("execute_central", {"code": "mock"})
    assert len(response) == 1
    
    result_data = json.loads(response[0].text)
    assert "_enrichment" not in result_data


def test_manual_analysis_prompt_definition(mock_server):
    """Ensure that the manual second-pass analysis prompt is defined in the block/module context."""
    from centralmind.server import manual_analysis_prompt
    assert "You are an expert network operations analyst" in manual_analysis_prompt
    assert "Provide rich, actionable insight" in manual_analysis_prompt
    
    assert hasattr(mock_server, "manual_analysis_prompt")
    assert mock_server.manual_analysis_prompt == manual_analysis_prompt


@pytest.mark.asyncio
async def test_enrichment_non_json_error_detection(mock_server):
    """Verify raw_output non-JSON error detection escalates blast radius."""
    raw_response = "Error: Unauthorized access to system resources"
    primary_result = [TextContent(type="text", text=raw_response)]
    
    response = await mock_server._perform_enrichment("central", primary_result)
    result_data = json.loads(response[0].text)
    assert "raw_output" in result_data
    assert result_data["raw_output"] == raw_response
    
    enrichment = result_data["_enrichment"]
    assert enrichment["blast_radius"] == "Medium"
    assert any("error" in r.lower() or "unauthorized" in r.lower() for r in enrichment["risks"])
    assert "manual_analysis_prompt" in enrichment


@pytest.mark.asyncio
async def test_enrichment_integer_boolean_status_checks(mock_server):
    """Verify integer boolean status representation like 0 is correctly caught as offline."""
    data = {
        "devices": [
            {"name": "AP-01", "online": 0, "connected": 1},
            {"name": "AP-02", "online": 1, "connected": 0},
            {"name": "AP-03", "online": "false", "connected": "true"}
        ]
    }
    primary_result = [TextContent(type="text", text=json.dumps(data))]
    
    response = await mock_server._perform_enrichment("central", primary_result)
    result_data = json.loads(response[0].text)
    enrichment = result_data["_enrichment"]
    
    assert enrichment["blast_radius"] == "High"
    assert enrichment["client_impact"]["count"] == 45
    assert "3 offline devices" in enrichment["impact_summary"]
    assert "manual_analysis_prompt" in enrichment
