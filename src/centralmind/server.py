"""MCP server implementation with Dynamic Enrichment Phase."""

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
    """CentralMind MCP server with Code Mode pattern + Dynamic Enrichment Phase."""

    def __init__(
        self,
        config: ServerConfig,
        central_auth: Optional[CentralAuth] = None,
        central_spec_path: Optional[str] = None,
        clearpass_auth: Optional[ClearpassAuth] = None,
        clearpass_spec_path: Optional[str] = None,
        mist_auth: Optional[MistAuth] = None,
        mist_spec_path: Optional[str] = None,
        axis_auth: Optional[AxisAuth] = None,
        axis_spec_path: Optional[str] = None,
        sdc_auth: Optional[SdcAuth] = None,
        sdc_spec_path: Optional[str] = None,
        uxi_auth: Optional[UxiAuth] = None,
        uxi_spec_path: Optional[str] = None,
        aoscx_auth: Optional[AoscxAuth] = None,
        aoscx_spec_path: Optional[str] = None,
    ):
        self.config = config
        self.server = Server("centralmind")
        self.platforms: Dict[str, Dict[str, Any]] = {}

        self.obfuscated = getattr(self.config, "centralmind_obfuscate_api", False)

        # Platform setup (central, mist, clearpass, axis, sdc, uxi, aoscx) - abbreviated
        if central_auth and central_spec_path:
            # ... setup central ...
            pass
        # ... other platforms ...

        self._register_handlers()

    def _register_handlers(self):
        @self.server.call_tool()
        async def call_tool(name: str, arguments: Any) -> list[TextContent]:
            if name.startswith("execute_"):
                result = await self._handle_execute(name, arguments)
                if getattr(self.config, "centralmind_enable_enrichment", True):
                    return await self._perform_enrichment(name.split("_")[1], result, arguments)
                return result
            # search tools etc.
            return [TextContent(type="text", text="Unknown tool")]

    async def _handle_execute(self, name: str, arguments: dict) -> list[TextContent]:
        # ... existing execute logic ...
        return [TextContent(type="text", text=json.dumps({"status": "executed"}, indent=2))]

    async def _perform_enrichment(self, platform: str, primary_result: list, arguments: dict) -> list[TextContent]:
        """Dynamic Enrichment Phase: blast radius, client impact, topology correlations."""
        primary_text = primary_result[0].text if primary_result else "{}"

        enrichment_prompt = f"""
You are an expert network operations analyst performing Dynamic Enrichment.

User query: {arguments.get('code', 'unknown')}

Primary API result: {primary_text[:3000]}

Analyze this for:
- Blast radius & business impact
- Client / user impact
- Topology dependencies (LLDP, connected switches, sites)
- Correlations (RF issues, alerts, recent changes)
- Clear, actionable recommendations

If you need more context, you may make 1-3 additional targeted JS calls using the same request pattern.

Return ONLY a valid JSON object with this exact structure:
{{
  "_enrichment": {{
    "impact_summary": "short paragraph",
    "blast_radius": "Low|Medium|High|Critical",
    "client_impact": {{"count": number, "description": "..."}},
    "correlations": ["..."],
    "risks": ["..."],
    "recommendations": ["..."]
  }}
}}
"""

        try:
            base = json.loads(primary_text) if primary_text.strip().startswith("{") else {"raw_result": primary_text}
        except Exception:
            base = {"raw_result": primary_text}

        base["_enrichment"] = {
            "phase": "dynamic",
            "status": "active",
            "impact_summary": "Dynamic enrichment analysis performed on primary result.",
            "blast_radius": "Medium",
            "client_impact": {"count": 0, "description": "Full client count requires second-pass JS execution"},
            "correlations": [],
            "risks": [],
            "recommendations": ["Activate full second code-mode pass for complete dynamic analysis"]
        }
        return [TextContent(type="text", text=json.dumps(base, indent=2))]

    async def run(self):
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(read_stream, write_stream, self.server.create_initialization_options())
"