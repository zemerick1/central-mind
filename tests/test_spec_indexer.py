"""Tests for spec_indexer module."""

import json
from pathlib import Path

import pytest

from centralmind.spec_indexer import (
    _count_operations,
    _detect_auth_pattern,
    _detect_pagination,
    _detect_response_patterns,
    _detect_scopes,
    _group_into_themes,
    generate_index,
    generate_index_from_file,
)


@pytest.fixture
def minimal_spec():
    """Minimal valid OpenAPI spec for testing."""
    # Need 5+ endpoints per scope to pass filtering
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/api/v1/self": {
                "get": {
                    "tags": ["Self"],
                    "summary": "Get current user",
                    "responses": {"200": {"description": "OK"}},
                }
            },
            # Add 5 Orgs endpoints to pass the filter
            "/api/v1/orgs/{org_id}/devices": {
                "get": {
                    "tags": ["Orgs Devices"],
                    "summary": "List devices",
                    "parameters": [
                        {"name": "limit", "in": "query"},
                        {"name": "start", "in": "query"},
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "results": {"type": "array"},
                                            "total": {"type": "integer"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/orgs/{org_id}/wlans": {
                "get": {"tags": ["Orgs WLANs"], "responses": {"200": {}}}
            },
            "/api/v1/orgs/{org_id}/sites": {
                "get": {"tags": ["Orgs Sites"], "responses": {"200": {}}}
            },
            "/api/v1/orgs/{org_id}/admins": {
                "get": {"tags": ["Orgs Admins"], "responses": {"200": {}}}
            },
            "/api/v1/orgs/{org_id}/settings": {
                "get": {"tags": ["Orgs Settings"], "responses": {"200": {}}}
            },
        },
    }


@pytest.fixture
def central_spec():
    """Load the real Aruba Central OpenAPI spec."""
    spec_path = Path(__file__).parent.parent / "spec" / "openAPI.resolved.json"
    if not spec_path.exists():
        spec_path = Path(__file__).parent.parent / "spec" / "openAPI.json"
    
    if not spec_path.exists():
        pytest.skip("Aruba Central spec not found")
    
    with open(spec_path) as f:
        return json.load(f)


class TestCountOperations:
    """Tests for _count_operations."""

    def test_counts_http_methods(self, minimal_spec):
        """Should count GET, POST, etc. methods."""
        count = _count_operations(minimal_spec)
        assert count == 6  # /self + 5 Orgs endpoints

    def test_ignores_non_http_methods(self):
        """Should ignore non-HTTP method keys like parameters, servers."""
        spec = {
            "paths": {
                "/test": {
                    "get": {},
                    "parameters": [],  # Not an HTTP method
                    "servers": [],  # Not an HTTP method
                }
            }
        }
        assert _count_operations(spec) == 1


class TestDetectScopes:
    """Tests for _detect_scopes."""

    def test_extracts_scopes_from_tags(self, minimal_spec):
        """Should extract scope prefixes from tags."""
        scopes = _detect_scopes(minimal_spec)
        assert "Orgs" in scopes
        # Self only has 1 endpoint, below the 5-endpoint threshold
        assert "Self" not in scopes
        assert scopes["Orgs"]["count"] == 5  # 5 Orgs endpoints

    def test_groups_categories_under_scopes(self, minimal_spec):
        """Should track categories within each scope."""
        scopes = _detect_scopes(minimal_spec)
        assert "Orgs Devices" in scopes["Orgs"]["categories"]
        # Count should match number of devices endpoints
        assert scopes["Orgs"]["categories"]["Orgs Devices"] >= 1

    def test_scope_prefix_extraction(self):
        """Should extract first word as scope prefix."""
        spec = {
            "paths": {
                f"/test{i}": {"get": {"tags": ["Alpha Beta"]}}
                for i in range(6)
            }
        }
        scopes = _detect_scopes(spec)
        assert "Alpha" in scopes

    def test_filters_small_scopes(self):
        """Should filter out scopes with < 5 endpoints (except named scopes)."""
        spec = {
            "paths": {
                f"/test{i}": {"get": {"tags": ["Tiny Scope"]}} for i in range(3)
            }
        }
        scopes = _detect_scopes(spec)
        assert "Tiny" not in scopes  # Only 3 endpoints


class TestDetectAuthPattern:
    """Tests for _detect_auth_pattern."""

    def test_finds_self_endpoint(self, minimal_spec):
        """Should detect /self endpoint as auth pattern."""
        pattern = _detect_auth_pattern(minimal_spec)
        assert "/api/v1/self" in pattern
        assert "Token-based" in pattern

    def test_prefers_shorter_auth_paths(self):
        """Should prefer /self over /orgs/123/self."""
        spec = {
            "paths": {
                "/api/v1/self": {"get": {}},
                "/api/v1/orgs/123/self": {"get": {}},
            }
        }
        pattern = _detect_auth_pattern(spec)
        assert "/api/v1/self" in pattern

    def test_checks_security_schemes_as_fallback(self):
        """Should check security schemes if no /self endpoint."""
        spec = {
            "paths": {},
            "components": {
                "securitySchemes": {
                    "bearerAuth": {"type": "http", "scheme": "bearer"}
                }
            },
        }
        pattern = _detect_auth_pattern(spec)
        assert "bearerAuth" in pattern


class TestDetectPagination:
    """Tests for _detect_pagination."""

    def test_finds_limit_start_end_params(self, minimal_spec):
        """Should detect limit, start, end parameters."""
        pagination = _detect_pagination(minimal_spec)
        assert "limit" in pagination
        assert "start" in pagination

    def test_returns_message_when_none_found(self):
        """Should return message if no pagination params."""
        spec = {"paths": {"/test": {"get": {"parameters": []}}}}
        pagination = _detect_pagination(spec)
        assert "No standard pagination" in pagination

    def test_counts_param_frequency(self):
        """Should count how many endpoints use each param."""
        spec = {
            "paths": {
                "/test1": {"get": {"parameters": [{"name": "limit"}]}},
                "/test2": {"get": {"parameters": [{"name": "limit"}]}},
                "/test3": {"get": {"parameters": [{"name": "offset"}]}},
            }
        }
        pagination = _detect_pagination(spec)
        assert "limit (2 endpoints)" in pagination


class TestDetectResponsePatterns:
    """Tests for _detect_response_patterns."""

    def test_detects_array_responses(self):
        """Should detect endpoints returning arrays."""
        spec = {
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "array"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        patterns = _detect_response_patterns(spec)
        assert any("Array responses" in p for p in patterns)

    def test_detects_paginated_responses(self, minimal_spec):
        """Should detect {results[], total} pattern."""
        patterns = _detect_response_patterns(minimal_spec)
        assert any("Paginated responses" in p for p in patterns)

    def test_detects_object_responses(self):
        """Should detect single object responses."""
        spec = {
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        patterns = _detect_response_patterns(spec)
        assert any("Single object" in p for p in patterns)


class TestGroupIntoThemes:
    """Tests for _group_into_themes."""

    def test_classifies_wlans_as_wireless(self):
        """WLANs should go to Wireless."""
        categories = {"WLANs": 10, "wlan-ssids": 5}
        themes = _group_into_themes(categories)
        assert "Wireless" in themes

    def test_classifies_firewall_as_security(self):
        """Firewall should go to Security."""
        categories = {"firewall": 5, "port-security": 3}
        themes = _group_into_themes(categories)
        assert "Security" in themes

    def test_classifies_bgp_as_routing(self):
        """BGP should go to Routing."""
        categories = {"bgp": 5, "ospfv2": 3}
        themes = _group_into_themes(categories)
        assert "Routing" in themes

    def test_classifies_vlan_as_switching(self):
        """VLAN should go to Switching."""
        categories = {"vlan-interfaces": 5, "stp": 3}
        themes = _group_into_themes(categories)
        assert "Switching" in themes

    def test_classifies_dhcp_as_network_services(self):
        """DHCP should go to Network Services."""
        categories = {"dhcp-server": 5, "dns": 3}
        themes = _group_into_themes(categories)
        assert "Network Services" in themes

    def test_classifies_api_tokens_as_other(self):
        """API Tokens should NOT go to Wireless (ap substring)."""
        categories = {"API Tokens": 3, "Orgs API Tokens": 2}
        themes = _group_into_themes(categories)
        assert "Wireless" not in themes or "API Tokens" not in themes.get("Wireless", {})
        assert "Other" in themes
        assert "API Tokens" in themes["Other"]

    def test_unmatched_goes_to_other(self):
        """Unknown categories should go to Other."""
        categories = {"custom-widget": 5}
        themes = _group_into_themes(categories)
        assert "Other" in themes
        assert "custom-widget" in themes["Other"]


class TestGenerateIndex:
    """Tests for generate_index."""

    def test_generates_valid_index(self, minimal_spec):
        """Should generate a valid index from minimal spec."""
        index = generate_index(minimal_spec)
        assert "Test API" in index
        assert "6 endpoints" in index  # Updated to match fixture
        assert "API HIERARCHY" in index
        assert "AUTH PATTERN" in index
        assert "PAGINATION" in index

    def test_includes_search_guide(self, minimal_spec):
        """Should include search guide instructions."""
        index = generate_index(minimal_spec)
        assert "SEARCH GUIDE" in index
        assert "ALWAYS search" in index

    def test_uses_theme_grouping_for_large_scopes(self, central_spec):
        """Should use theme grouping for scopes with 10+ categories."""
        index = generate_index(central_spec)
        # Should contain the API hierarchy
        assert "API HIERARCHY" in index
        # Should have at least some of the Aruba-specific themes
        has_themes = any(theme in index for theme in [
            "Wireless:", "Security:", "Routing:", "Switching:",
            "Network Services:", "Monitoring:", "Other:"
        ])
        assert has_themes, f"No themes found in index"


class TestCentralSpecSmoke:
    """Smoke tests with the real Aruba Central spec."""

    def test_total_endpoint_count(self, central_spec):
        """Total should be a substantial number of endpoints."""
        total = _count_operations(central_spec)
        # Aruba Central has 1000+ endpoints
        assert total > 500, f"Total {total} seems too low"

    def test_index_size_reasonable(self, central_spec):
        """Index should be reasonably sized for LLM context."""
        index = generate_index(central_spec)
        length = len(index)
        assert 1000 <= length <= 10000, f"Index length {length} out of range"

    def test_contains_always_search_instruction(self, central_spec):
        """Should contain ALWAYS search instruction."""
        index = generate_index(central_spec)
        assert "ALWAYS search" in index


class TestGenerateIndexFromFile:
    """Tests for generate_index_from_file."""

    def test_loads_and_generates_from_file(self, tmp_path):
        """Should load spec from file and generate index."""
        spec_file = tmp_path / "test.json"
        spec = {
            "openapi": "3.1.0",
            "info": {"title": "File Test", "version": "1.0"},
            "paths": {"/test": {"get": {"tags": ["Test"]}}},
        }
        spec_file.write_text(json.dumps(spec))

        index = generate_index_from_file(str(spec_file))
        assert "File Test" in index
        assert "1 endpoints" in index
