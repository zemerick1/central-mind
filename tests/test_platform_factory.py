"""Tests for platform_factory.resolve_api_mode — the server-ceiling logic
behind per-client API mode overrides."""

from centralmind.platform_factory import resolve_api_mode


class TestResolveApiMode:
    def test_no_override_uses_server_ceiling(self):
        assert resolve_api_mode("readonly", None) == "readonly"
        assert resolve_api_mode("readwrite", None) == "readwrite"
        assert resolve_api_mode("all", None) == "all"

    def test_override_within_ceiling_is_honored(self):
        assert resolve_api_mode("all", "readonly") == "readonly"
        assert resolve_api_mode("all", "readwrite") == "readwrite"
        assert resolve_api_mode("readwrite", "readonly") == "readonly"

    def test_override_exceeding_ceiling_is_capped(self):
        # The whole point: a client can never get more access than the
        # server itself allows, no matter what its own override says.
        assert resolve_api_mode("readonly", "readwrite") == "readonly"
        assert resolve_api_mode("readonly", "all") == "readonly"
        assert resolve_api_mode("readwrite", "all") == "readwrite"

    def test_override_equal_to_ceiling(self):
        assert resolve_api_mode("readwrite", "readwrite") == "readwrite"

    def test_invalid_override_falls_back_to_ceiling(self):
        assert resolve_api_mode("readwrite", "not-a-real-mode") == "readwrite"
        assert resolve_api_mode("readonly", "") == "readonly"

    def test_invalid_server_ceiling_defaults_to_readonly(self):
        # Defensive: an unrecognized server ceiling should never silently
        # resolve to something more permissive than the safest default.
        assert resolve_api_mode("not-a-real-mode", "all") == "readonly"
