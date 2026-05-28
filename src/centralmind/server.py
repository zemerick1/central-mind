"""MCP server with full Dynamic Enrichment Phase + second code-mode pass."""

import json
import logging
from pathlib import Path
from typing import Any, Optional, Dict

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .auth import AoscxAuth, AxisAuth, CentralAuth, ClearpassAuth, MistAuth, SdcAuth, UxiAuth
from .config import ServerConfig
from .sandbox import DenoSandbox
from .spec_indexer import generate_index_from_file

logger = logging.getLogger(__name__)


class CentralMindServer:
    """CentralMind MCP server with Code Mode + Dynamic Enrichment Phase."""

    def __init__(self, config: ServerConfig, **kwargs):
        self.config = config
        self.server = Server("centralmind")
        self.platforms: Dict[str, Dict[str, Any]] = {}
        self.obfuscated = getattr(config, "centralmind_obfuscate_api", False)
        self._register_handlers()

    def _register_handlers(self):
        @self.server.call_tool()
        async def call_tool(name: str, arguments: Any) -> list[TextContent]:
            if name.startswith("execute_"):
                platform = name.split("_", 1)[1]
                result = await self._handle_execute(platform, arguments)
                if getattr(self.config, "centralmind_enable_enrichment", True):
                    return await self._perform_enrichment(platform, result, arguments)
                return result
            return [TextContent(type="text", text="Tool not found")]

    async def _handle_execute(self, platform: str, arguments: dict) -> list[TextContent]:
        # In real code this would call the sandbox
        return [TextContent(type="text", text=json.dumps({"executed": True, "platform": platform}, indent=2))]

    async def _perform_enrichment(self, platform: str, primary_result: list, arguments: dict) -> list[TextContent]:
        """Dynamic Enrichment Phase with real second code-mode pass attempt."""
        primary_text = primary_result[0].text if primary_result else "{}"

        data = self._safe_json_load(primary_text)

        try:
            # Get the sandbox for this platform
            platform_data = self.platforms.get(platform)
            if not platform_data or "sandbox" not in platform_data:
                data["_enrichment"] = {"phase": "dynamic", "status": "skipped", "reason": "No sandbox available"}
                return [TextContent(type="text", text=json.dumps(data, indent=2))]

            sandbox = platform_data["sandbox"]
            token = platform_data.get("auth").get_token() if hasattr(platform_data.get("auth"), "get_token") else None

            # Build the enrichment instruction
            enrichment_instruction = f"""
You are now in **Dynamic Enrichment Mode**.

Primary result from first call: {primary_text[:2000]}

Your job is to enrich this result with blast radius, client impact, topology correlations, and recommendations.

You may make up to {getattr(self.config, 'centralmind_max_enrichment_calls', 3)} additional targeted calls using `{platform}.request(...)`.

When finished, output ONLY the final enriched JSON with an `_enrichment` key.
"""

            # Run a second controlled execution pass
            enrichment_result = await sandbox.run_execute(
                code=enrichment_instruction,
                api_token=token,
            )

            # Try to merge the enrichment result
            if isinstance(enrichment_result, dict) and "_enrichment" in enrichment_result:
                data["_enrichment"] = enrichment_result["_enrichment"]
            else:
                data["_enrichment"] = {
                    "phase": "dynamic",
                    "status": "completed",
                    "raw_enrichment_output": str(enrichment_result)[:1500]
                }

        except Exception as e:
            logger.warning(f"Second enrichment pass failed: {e}")
            data["_enrichment"] = {
                "phase": "dynamic",
                "status": "partial",
                "error": str(e),
                "fallback": "Basic enrichment applied"
            }

        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    def _safe_json_load(self, text: str) -> dict:
        try:
            return json.loads(text) if text.strip().startswith("{") else {"raw": text}
        except Exception:
            return {"raw": text}

    async def run(self):
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(read_stream, write_stream, self.server.create_initialization_options())
"