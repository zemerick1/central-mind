"""Tests for spec_fetcher module."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from centralmind.spec_fetcher import (
    _looks_like_oas,
    _slugify,
    discover_specs,
    fetch_spec,
    merge_specs,
)


class TestSlugify:
    """Tests for _slugify helper."""

    def test_basic_title(self):
        assert _slugify("Monitoring APIs") == "monitoring-apis"

    def test_special_characters(self):
        assert _slugify("HPE Aruba Networking Central — MRT") == "hpe-aruba-networking-central-mrt"

    def test_empty_string(self):
        assert _slugify("") == "untitled"

    def test_whitespace_only(self):
        assert _slugify("   ") == "untitled"

    def test_already_slug(self):
        assert _slugify("already-a-slug") == "already-a-slug"


class TestLooksLikeOas:
    """Tests for _looks_like_oas validator."""

    def test_valid_openapi3(self):
        assert _looks_like_oas({
            "openapi": "3.1.0",
            "paths": {"/foo": {"get": {}}},
        })

    def test_valid_swagger2(self):
        assert _looks_like_oas({
            "swagger": "2.0",
            "paths": {"/foo": {"get": {}}},
        })

    def test_no_paths(self):
        assert not _looks_like_oas({
            "openapi": "3.1.0",
            "paths": {},
        })

    def test_not_a_dict(self):
        assert not _looks_like_oas("not a spec")
        assert not _looks_like_oas([])
        assert not _looks_like_oas(None)

    def test_missing_version(self):
        assert not _looks_like_oas({
            "paths": {"/foo": {}},
        })


class TestMergeSpecs:
    """Tests for merge_specs."""

    @pytest.fixture
    def mrt_spec(self):
        return {
            "openapi": "3.1.0",
            "info": {"title": "MRT APIs", "version": "1.0"},
            "tags": [
                {"name": "Monitoring > APs", "description": "AP APIs"},
                {"name": "Monitoring > Clients", "description": "Client APIs"},
            ],
            "paths": {
                "/monitoring/v2/aps": {
                    "get": {"operationId": "getAPs", "summary": "Get APs"}
                },
                "/monitoring/v2/clients": {
                    "get": {"operationId": "getClients", "summary": "Get clients"}
                },
            },
            "components": {
                "schemas": {
                    "AP": {"type": "object", "properties": {"serial": {"type": "string"}}},
                },
                "responses": {
                    "HTTP400": {"description": "Bad Request"},
                },
            },
        }

    @pytest.fixture
    def config_spec(self):
        return {
            "openapi": "3.1.0",
            "info": {"title": "Config APIs", "version": "1.0"},
            "tags": [
                {"name": "Wireless > Wlan", "description": "WLAN APIs"},
                # Duplicate tag from MRT — should be deduplicated
                {"name": "Monitoring > APs", "description": "AP APIs"},
            ],
            "paths": {
                "/network-config/v1/wlan": {
                    "get": {"operationId": "getWlans", "summary": "Get WLANs"}
                },
            },
            "components": {
                "schemas": {
                    "Wlan": {"type": "object", "properties": {"ssid": {"type": "string"}}},
                    # Duplicate from MRT — should keep first
                    "AP": {"type": "object", "properties": {"serial": {"type": "string"}}},
                },
                "responses": {
                    "HTTP400": {"description": "Bad Request"},
                    "HTTP401": {"description": "Unauthorized"},
                },
            },
        }

    def test_paths_merged(self, mrt_spec, config_spec):
        merged = merge_specs([mrt_spec, config_spec])
        assert "/monitoring/v2/aps" in merged["paths"]
        assert "/monitoring/v2/clients" in merged["paths"]
        assert "/network-config/v1/wlan" in merged["paths"]
        assert len(merged["paths"]) == 3

    def test_tags_deduplicated(self, mrt_spec, config_spec):
        merged = merge_specs([mrt_spec, config_spec])
        tag_names = [t["name"] for t in merged["tags"]]
        assert len(tag_names) == len(set(tag_names)), "Tags should be deduplicated"
        assert "Monitoring > APs" in tag_names
        assert "Monitoring > Clients" in tag_names
        assert "Wireless > Wlan" in tag_names

    def test_schemas_merged(self, mrt_spec, config_spec):
        merged = merge_specs([mrt_spec, config_spec])
        schemas = merged["components"]["schemas"]
        assert "AP" in schemas
        assert "Wlan" in schemas

    def test_responses_merged(self, mrt_spec, config_spec):
        merged = merge_specs([mrt_spec, config_spec])
        responses = merged["components"]["responses"]
        assert "HTTP400" in responses
        assert "HTTP401" in responses

    def test_custom_title(self, mrt_spec):
        merged = merge_specs([mrt_spec], title="Custom Title")
        assert merged["info"]["title"] == "Custom Title"

    def test_default_title(self, mrt_spec):
        merged = merge_specs([mrt_spec])
        assert merged["info"]["title"] == "HPE Aruba Networking Central API"

    def test_openapi_version(self, mrt_spec):
        merged = merge_specs([mrt_spec])
        assert merged["openapi"] == "3.1.0"

    def test_security_scheme_present(self, mrt_spec):
        merged = merge_specs([mrt_spec])
        assert "BearerAuth" in merged["components"]["securitySchemes"]

    def test_empty_specs_list(self):
        merged = merge_specs([])
        assert merged["paths"] == {}
        assert merged["components"]["schemas"] == {}

    def test_tags_sorted_by_name(self, mrt_spec, config_spec):
        merged = merge_specs([mrt_spec, config_spec])
        tag_names = [t["name"] for t in merged["tags"]]
        assert tag_names == sorted(tag_names)


class TestDiscoverSpecs:
    """Tests for discover_specs (mocked HTTP)."""

    @pytest.fixture
    def mock_ssr_props(self):
        """SSR props HTML payload mimicking ReadMe SuperHub."""
        props = {
            "apiDefinitions": [
                {"filename": "monitoring-v2.json", "type": "openapi"},
                {"filename": "reporting-v1.json", "type": "openapi"},
            ],
            "context": {
                "project": {
                    "stable": {
                        "apiRegistries": [
                            {"filename": "monitoring-v2.json", "uuid": "uuid-mon-001"},
                            {"filename": "reporting-v1.json", "uuid": "uuid-rpt-001"},
                            # Older version — should be overridden
                            {"filename": "monitoring-v2.json", "uuid": "uuid-mon-old"},
                        ]
                    }
                }
            },
        }
        return f'<script id="ssr-props">{json.dumps(props)}</script>'

    @patch("centralmind.spec_fetcher._http_get")
    def test_discovers_specs(self, mock_get, mock_ssr_props):
        mock_get.return_value = mock_ssr_props.encode()
        specs = discover_specs("new-central")

        assert len(specs) == 2
        filenames = {s["filename"] for s in specs}
        assert "monitoring-v2.json" in filenames
        assert "reporting-v1.json" in filenames

    @patch("centralmind.spec_fetcher._http_get")
    def test_uuid_latest_wins(self, mock_get, mock_ssr_props):
        """Later registry entries (more recent) should override earlier ones."""
        mock_get.return_value = mock_ssr_props.encode()
        specs = discover_specs("new-central")

        # The uuid for monitoring-v2.json should NOT be uuid-mon-old
        # because uuid-mon-old appears BEFORE uuid-mon-001 in the registries
        # list, and later entries override. Wait — actually it's the opposite:
        # uuid-mon-old appears AFTER uuid-mon-001, so uuid-mon-old wins.
        # Let's check what the fixture actually has.
        mon_spec = next(s for s in specs if s["filename"] == "monitoring-v2.json")
        # uuid-mon-old is last in the list, so it overrides uuid-mon-001
        assert mon_spec["uuid"] == "uuid-mon-old"

    @patch("centralmind.spec_fetcher._http_get")
    def test_no_ssr_props_raises(self, mock_get):
        mock_get.return_value = b"<html><body>No props here</body></html>"
        with pytest.raises(RuntimeError, match="no ssr-props"):
            discover_specs("new-central")

    @patch("centralmind.spec_fetcher._http_get")
    def test_no_definitions_raises(self, mock_get):
        props = {"apiDefinitions": [], "context": {"project": {"stable": {"apiRegistries": []}}}}
        html = f'<script id="ssr-props">{json.dumps(props)}</script>'
        mock_get.return_value = html.encode()
        with pytest.raises(RuntimeError, match="no OpenAPI definitions"):
            discover_specs("new-central")


class TestFetchSpec:
    """Tests for fetch_spec (mocked HTTP)."""

    @patch("centralmind.spec_fetcher._http_get")
    def test_valid_spec(self, mock_get):
        spec = {"openapi": "3.1.0", "info": {"title": "Test"}, "paths": {"/foo": {}}}
        mock_get.return_value = json.dumps(spec).encode()
        result = fetch_spec("some-uuid")
        assert result is not None
        assert result["openapi"] == "3.1.0"

    @patch("centralmind.spec_fetcher._http_get")
    def test_invalid_json(self, mock_get):
        mock_get.return_value = b"not json"
        result = fetch_spec("some-uuid")
        assert result is None

    @patch("centralmind.spec_fetcher._http_get")
    def test_not_oas(self, mock_get):
        mock_get.return_value = json.dumps({"foo": "bar"}).encode()
        result = fetch_spec("some-uuid")
        assert result is None
