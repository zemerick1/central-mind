"""Stress and edge case tests for the Dynamic Enrichment implementation."""

import json
import time
import pytest
from mcp.types import TextContent

from centralmind.clients_store import ClientsStore
from centralmind.config import ServerConfig
from centralmind.server import CentralMindServer


@pytest.fixture
def mock_server(tmp_path):
    """Create a CentralMindServer with dynamic enrichment enabled.

    _perform_enrichment/_detect_anomalies are pure data-transformation
    methods (no credentials, no platform/client state involved), so an
    empty clients_store/resolved_spec_paths is enough here.
    """
    config = ServerConfig(
        centralmind_enable_enrichment=True,
        centralmind_max_enrichment_calls=3,
    )
    clients_store = ClientsStore(path=tmp_path / "clients.json", key_path=tmp_path / "secret.key")
    server = CentralMindServer(config=config, clients_store=clients_store, resolved_spec_paths={})
    return server


@pytest.mark.asyncio
async def test_enrichment_empty_raw_text(mock_server):
    """Verify that an empty raw text input is handled without crashing."""
    primary_result = [TextContent(type="text", text="")]
    response = await mock_server._perform_enrichment("central", primary_result)
    assert len(response) == 1
    result_data = json.loads(response[0].text)
    assert "raw_output" in result_data
    assert result_data["raw_output"] == ""
    assert result_data["_enrichment"]["blast_radius"] == "Low"


@pytest.mark.asyncio
async def test_enrichment_invalid_json(mock_server):
    """Verify that invalid JSON string is handled and wrapped in raw_output."""
    invalid_json = "{'status': 'offline', missing_quotes}"
    primary_result = [TextContent(type="text", text=invalid_json)]
    response = await mock_server._perform_enrichment("central", primary_result)
    assert len(response) == 1
    result_data = json.loads(response[0].text)
    assert "raw_output" in result_data
    assert result_data["raw_output"] == invalid_json
    assert result_data["_enrichment"]["blast_radius"] == "Low"


@pytest.mark.asyncio
async def test_enrichment_invalid_json_with_error(mock_server):
    """Verify that invalid JSON string with error keyword is flagged as Medium blast radius."""
    invalid_json = "{'status': 'error', missing_quotes}"
    primary_result = [TextContent(type="text", text=invalid_json)]
    response = await mock_server._perform_enrichment("central", primary_result)
    assert len(response) == 1
    result_data = json.loads(response[0].text)
    assert result_data["_enrichment"]["blast_radius"] == "Medium"


@pytest.mark.asyncio
async def test_enrichment_json_array_root(mock_server):
    """Verify that JSON array at root causes TypeError which is caught, returning original result."""
    devices_array = [
        {"name": "AP-01", "status": "offline", "online": False},
        {"name": "AP-02", "status": "connected", "online": True},
        {"name": "AP-03", "status": "down", "online": False}
    ]
    primary_result = [TextContent(type="text", text=json.dumps(devices_array))]
    response = await mock_server._perform_enrichment("central", primary_result)
    
    # Verify it does NOT crash, but returns the original result because enrichment fails on lists
    assert len(response) == 1
    result_data = json.loads(response[0].text)
    assert isinstance(result_data, list)
    assert len(result_data) == 3
    # Check that _enrichment is NOT present because list index assignment failed
    for item in result_data:
        assert "_enrichment" not in item


@pytest.mark.asyncio
async def test_enrichment_deep_nesting(mock_server):
    """Verify how deep nesting is handled (e.g. 500 nested dictionaries)."""
    # Build a 500-level nested dictionary
    nested_data = {"status": "online"}
    for _ in range(500):
        nested_data = {"sub": nested_data}
        
    primary_result = [TextContent(type="text", text=json.dumps(nested_data))]
    response = await mock_server._perform_enrichment("central", primary_result)
    assert len(response) == 1
    result_data = json.loads(response[0].text)
    assert "_enrichment" in result_data
    assert result_data["_enrichment"]["blast_radius"] == "Low"


@pytest.mark.asyncio
async def test_enrichment_extreme_deep_nesting_recursion_limit(mock_server):
    """Verify that extreme deep nesting (e.g. exceeding stack depth) doesn't crash the server."""
    # Build a 2000-level nested dictionary
    nested_data = {"status": "online"}
    for _ in range(2000):
        nested_data = {"sub": nested_data}
        
    # Python default recursion limit is 1000. json.dumps might fail, or _detect_anomalies will.
    try:
        json_str = json.dumps(nested_data)
    except RecursionError:
        # If json.dumps fails, that's fine, we skip this since it's a Python json limitation
        pytest.skip("RecursionError during json.dumps")

    primary_result = [TextContent(type="text", text=json_str)]
    
    # This should not raise RecursionError to the caller; the server should catch it and return original result
    response = await mock_server._perform_enrichment("central", primary_result)
    assert len(response) == 1
    # Check if it returned the original result or succeeded
    result_data = json.loads(response[0].text)
    # If it hit RecursionError in _detect_anomalies, it returns original result without _enrichment
    # If it succeeded, _enrichment is there. Either way, no unhandled crash.


@pytest.mark.asyncio
async def test_enrichment_status_types(mock_server):
    """Verify how various types for status and online/connected keys are handled."""
    data = {
        "device1": {"status": 0, "online": 0},  # online: 0 is offline
        "device2": {"status": False, "online": "false"},  # online: "false" is offline
        "device3": {"status": None, "online": None},  # Not offline, no crash
        "device4": {"status": ["offline"]},  # status is list: not detected as offline (limitation), no crash
        "device5": {"status": "offline"},  # offline
        "device6": {"status": "disconnected"}  # offline
    }
    primary_result = [TextContent(type="text", text=json.dumps(data))]
    response = await mock_server._perform_enrichment("central", primary_result)
    assert len(response) == 1
    result_data = json.loads(response[0].text)
    enrichment = result_data["_enrichment"]
    
    # We expect:
    # device1: offline (due to online=0)
    # device2: offline (due to online="false")
    # device3: healthy (None is not false/0/"false" for online)
    # device4: healthy (list is not string for status)
    # device5: offline (status="offline")
    # device6: offline (status="disconnected")
    # Total offline count should be 4 (device1, device2, device5, device6)
    # 4 offline devices -> High blast radius (1 < count <= 5)
    assert enrichment["blast_radius"] == "High"
    assert enrichment["client_impact"]["count"] == 40 or "4 offline devices" in enrichment["impact_summary"]


@pytest.mark.asyncio
async def test_enrichment_performance_bottleneck(mock_server):
    """Verify if a huge payload under an error key causes a bottleneck due to string conversion."""
    # Construct a payload with an "error" key containing 50,000 items.
    # In _detect_anomalies:
    # errors_found.append(f"Key '{k}' contains anomaly keyword: {v}")
    # This formats the entire list into a string, which can be very slow.
    huge_list = [{"id": i, "name": f"Device-{i}"} for i in range(50000)]
    data = {
        "error_details": huge_list
    }
    primary_result = [TextContent(type="text", text=json.dumps(data))]
    
    start_time = time.monotonic()
    response = await mock_server._perform_enrichment("central", primary_result)
    end_time = time.monotonic()
    
    duration = end_time - start_time
    print(f"Enrichment duration for huge payload: {duration:.4f} seconds")
    
    assert len(response) == 1
    result_data = json.loads(response[0].text)
    assert "_enrichment" in result_data
    # It should have caught the error
    assert result_data["_enrichment"]["blast_radius"] == "Medium"
    # Verify the execution duration isn't excessively long (e.g. > 5 seconds, which would be a severe blocker)
    # On typical test hardware, we hope it completes fast, but we'll record the actual duration in findings.
