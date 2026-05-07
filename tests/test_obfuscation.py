"""Test that CentralMind works on obfuscated (private/unknown) APIs.

This is the KEY test that proves CentralMind doesn't depend on training data
about specific APIs. We obfuscate the Aruba Central spec completely (rename
paths, tags, operations) while keeping the structure intact, then verify the
spec_indexer can still auto-detect the hierarchy and the sandbox can
still discover and execute searches.
"""

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from centralmind.obfuscator import (
    obfuscate_spec_file,
    generate_deobfuscation_js,
    PATH_MAPPING,
    REVERSE_PATH_MAPPING,
)
from centralmind.spec_indexer import generate_index_from_file


def _find_deno() -> str:
    """Auto-detect Deno binary path."""
    deno = shutil.which("deno")
    if deno:
        return deno
    home_deno = Path.home() / ".deno" / "bin" / "deno"
    if home_deno.exists():
        return str(home_deno)
    pytest.skip("Deno not found")


class TestObfuscation:
    """Test CentralMind on obfuscated API specs."""

    @pytest.fixture
    def obfuscated_spec_path(self):
        """Create an obfuscated version of the Aruba Central spec."""
        spec_dir = Path(__file__).parent.parent / "spec"
        # Try resolved first, then raw
        original_path = spec_dir / "openAPI.resolved.json"
        if not original_path.exists():
            original_path = spec_dir / "openAPI.json"
        if not original_path.exists():
            pytest.skip("No spec file found")

        obfuscated_path = obfuscate_spec_file(original_path)
        yield str(obfuscated_path)
        obfuscated_path.unlink(missing_ok=True)

    # ── Index generation tests ───────────────────────────────────────

    def test_obfuscated_spec_generates_valid_index(self, obfuscated_spec_path):
        """spec_indexer can generate a valid index from obfuscated spec."""
        index = generate_index_from_file(obfuscated_spec_path)

        assert "Obfuscated Test API" in index
        assert "endpoints" in index
        assert "=== API HIERARCHY ===" in index
        assert "=== AUTH PATTERN ===" in index
        assert "=== PAGINATION ===" in index
        assert "=== RESPONSE PATTERNS ===" in index
        assert "=== SEARCH GUIDE ===" in index

    def test_obfuscated_spec_detects_auth(self, obfuscated_spec_path):
        """Auth endpoint is detected even after obfuscation."""
        index = generate_index_from_file(obfuscated_spec_path)
        assert "Token-based" in index or "Auth" in index

    def test_obfuscated_spec_detects_pagination(self, obfuscated_spec_path):
        index = generate_index_from_file(obfuscated_spec_path)
        assert "=== PAGINATION ===" in index

    def test_force_search_first_flag(self, obfuscated_spec_path):
        """force_search_first adds stronger instructions."""
        normal = generate_index_from_file(obfuscated_spec_path, force_search_first=False)
        forced = generate_index_from_file(obfuscated_spec_path, force_search_first=True)

        assert "CRITICAL INSTRUCTIONS:" in forced
        assert "CRITICAL INSTRUCTIONS:" not in normal
        assert "ALWAYS search" in normal

    # ── Sandbox search tests ─────────────────────────────────────────

    def test_search_works_on_obfuscated_spec(self, obfuscated_spec_path):
        """Can discover endpoints in a completely renamed API."""
        from centralmind.sandbox import DenoSandbox

        sandbox = DenoSandbox(deno_path=_find_deno(), timeout=30, api_mode="readonly")

        code = """
        async () => {
            // Just count paths to verify spec is loadable
            return Object.keys(spec.paths).length;
        }
        """

        result = asyncio.run(sandbox.run_search(code=code, spec_path=obfuscated_spec_path))

        if isinstance(result, dict) and "error" in result:
            pytest.fail(f"Search failed: {result['error']}")

        assert isinstance(result, int)
        assert result > 100, "Should find many endpoints"

    def test_obfuscated_spec_structure_intact(self, obfuscated_spec_path):
        """Verify obfuscation preserves spec structure (params, schemas, etc)."""
        with open(obfuscated_spec_path, "r") as f:
            spec = json.load(f)

        assert "paths" in spec
        assert "info" in spec
        assert len(spec["paths"]) > 100

        # Sample a GET endpoint — should still have tags/summary
        for path, methods in spec["paths"].items():
            if "get" in methods:
                op = methods["get"]
                assert "tags" in op or "summary" in op
                break

    # ── De-obfuscation mapping tests ─────────────────────────────────

    def test_deobfuscation_js_generated(self):
        """generate_deobfuscation_js produces valid JS with all mappings."""
        js = generate_deobfuscation_js()
        assert "__pathMapping" in js
        # Verify the actual Aruba Central obfuscation mappings
        for obf, real in REVERSE_PATH_MAPPING.items():
            assert f"'{obf}': '{real}'" in js

    def test_deobfuscation_round_trip(self):
        """Obfuscated paths correctly map back to real ones."""
        # Every entry in PATH_MAPPING must have a reverse
        for real, obf in PATH_MAPPING.items():
            assert obf in REVERSE_PATH_MAPPING
            assert REVERSE_PATH_MAPPING[obf] == real

    def test_all_mappings_present(self):
        """All expected mappings exist."""
        # Core Aruba Central terms that should be mapped
        assert "wlan" in PATH_MAPPING
        assert "vlan" in PATH_MAPPING
        assert "bgp" in PATH_MAPPING
        assert "firewall" in PATH_MAPPING
        assert "dhcp" in PATH_MAPPING
        assert "certificate" in PATH_MAPPING


if __name__ == "__main__":
    """Run obfuscation tests and print the obfuscated index."""
    from centralmind.obfuscator import obfuscate_spec_file

    spec_dir = Path(__file__).parent.parent / "spec"
    original_path = spec_dir / "openAPI.resolved.json"
    if not original_path.exists():
        original_path = spec_dir / "openAPI.json"

    print("Creating obfuscated spec...")
    obf_path = obfuscate_spec_file(original_path)

    print(f"Obfuscated spec written to: {obf_path}\n")

    print("=== OBFUSCATED SPEC INDEX ===\n")
    index = generate_index_from_file(str(obf_path))
    print(index)

    print(f"\n\n=== STATS ===\n")
    print(f"Characters: {len(index)}")
    print(f"Estimated tokens: ~{len(index) // 4}")

    obf_path.unlink(missing_ok=True)
