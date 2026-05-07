"""MCP server implementation with search and execute tools."""

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .auth import CentralAuth
from .config import ServerConfig
from .sandbox import DenoSandbox
from .spec_indexer import generate_index_from_file

logger = logging.getLogger(__name__)


class CentralMindServer:
    """CentralMind MCP server with Code Mode pattern."""

    def __init__(self, config: ServerConfig, auth: CentralAuth, spec_path: str):
        """Initialize server with config, auth manager, and resolved spec path."""
        self.config = config
        self.auth = auth
        self.spec_path = Path(spec_path)
        self.sandbox = DenoSandbox(
            deno_path=config.deno_path,
            api_host=auth.host,
            timeout=30,
            api_mode=config.centralmind_api_mode,
            rate_limit=config.centralmind_rate_limit,
            max_concurrent=config.centralmind_max_concurrent,
            obfuscated=getattr(config, "centralmind_obfuscate_api", False),
        )
        self.server = Server("centralmind")
        
        # Verify spec exists
        if not self.spec_path.exists():
            raise FileNotFoundError(
                f"Resolved spec not found at {self.spec_path}. "
                f"Please run: python -m centralmind.spec_resolver "
                f"spec/openAPI.json spec/openAPI.resolved.json"
            )
        
        # Apply runtime obfuscation if configured
        self.obfuscated = getattr(self.config, "centralmind_obfuscate_api", False)
        if self.obfuscated:
            logger.warning(
                "⚠️  Runtime API Obfuscation ENABLED — "
                "the LLM will see fictional resource names."
            )
            from .obfuscator import obfuscate_spec_file
            self.spec_path = obfuscate_spec_file(self.spec_path)
        
        # Generate dynamic index from spec
        logger.info("Generating spec index...")
        self.spec_index = generate_index_from_file(
            str(self.spec_path), force_search_first=self.obfuscated
        )
        logger.info(f"Spec index generated (~{len(self.spec_index) // 4} tokens)")
        
        self._register_handlers()

    def _register_handlers(self):
        """Register MCP tool handlers."""
        
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """List available tools."""
            # Adapt descriptions based on obfuscation mode
            if self.obfuscated:
                search_desc = (
                    "JavaScript async arrow function to search the OpenAPI spec. "
                    "Example: async () => { const results = []; for (const [path, methods] "
                    "of Object.entries(spec.paths)) { for (const [method, op] of "
                    "Object.entries(methods)) { if (op.tags?.some(t => "
                    't.toLowerCase().includes("wireless"))) results.push({method: '
                    "method.toUpperCase(), path, summary: op.summary}); } } return results; }"
                )
                execute_desc = (
                    "Execute JS against the API. Use central.request({method, path, body, params}).\n"
                    "IMPORTANT: You MUST use the `search` tool first to find exact paths and "
                    "parameters — your pre-trained knowledge of this API will not apply.\n"
                    "method defaults to GET. Chain multiple calls, filter/transform results in JS.\n"
                    "central.allowedMethods shows permitted HTTP methods.\n"
                    "For paginated results: check if total > results.length, loop with page/start params."
                )
                execute_example = (
                    "JavaScript async arrow function to execute. "
                    "Paths must include their full prefix from the spec (e.g. /network-monitoring/v1/... or /network-config/v1/...). "
                    'Example: async () => { const result = await central.request({path: "/network-monitoring/v1/aps", params: {limit: 5}}); '
                    "return result; }"
                )
            else:
                search_desc = (
                    "JavaScript async arrow function to search the OpenAPI spec. "
                    "Example: async () => { const results = []; for (const [path, methods] "
                    "of Object.entries(spec.paths)) { for (const [method, op] of "
                    "Object.entries(methods)) { if (op.tags?.some(t => "
                    't.toLowerCase().includes("wlan"))) results.push({method: '
                    "method.toUpperCase(), path, summary: op.summary}); } } return results; }"
                )
                execute_desc = (
                    "Execute JS against the Aruba Central API. Use central.request({method, path, body, params}).\n"
                    "method defaults to GET. Chain multiple calls, filter/transform results in JS.\n"
                    "central.allowedMethods shows permitted HTTP methods.\n"
                    "For paginated results: check if total > results.length, loop with page/start params.\n"
                    "For write ops: return a preview first, execute write only after user confirms."
                )
                execute_example = (
                    "JavaScript async arrow function to execute. "
                    "Paths must include their full prefix from the spec (e.g. /network-monitoring/v1/... or /network-config/v1/...). "
                    'Example: async () => { const result = await central.request({path: "/network-monitoring/v1/aps", params: {limit: 5}}); '
                    "return result; }"
                )

            return [
                Tool(
                    name="search",
                    description=self.spec_index,
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": search_desc,
                            }
                        },
                        "required": ["code"],
                    },
                ),
                Tool(
                    name="execute",
                    description=execute_desc,
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": execute_example,
                            }
                        },
                        "required": ["code"],
                    },
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Any) -> list[TextContent]:
            """Handle tool calls."""
            try:
                if name == "search":
                    return await self._handle_search(arguments)
                elif name == "execute":
                    return await self._handle_execute(arguments)
                else:
                    return [
                        TextContent(
                            type="text",
                            text=f"Unknown tool: {name}",
                        )
                    ]
            except Exception as e:
                logger.error(f"Tool call error: {e}", exc_info=True)
                error_msg = str(e)
                # Scrub token from exception messages
                current_token = self.auth._access_token
                if current_token:
                    error_msg = error_msg.replace(current_token, "[REDACTED]")
                return [
                    TextContent(
                        type="text",
                        text=f"Error: {error_msg}",
                    )
                ]

    async def _handle_search(self, arguments: dict) -> list[TextContent]:
        """Handle search tool call."""
        code = arguments.get("code")
        if not code:
            return [TextContent(type="text", text="Error: 'code' parameter required")]
        
        logger.info(f"Executing search with code length: {len(code)}")
        
        result = await self.sandbox.run_search(
            code=code,
            spec_path=str(self.spec_path),
        )
        
        # Format result as text
        result_text = json.dumps(result, indent=2)
        
        return [TextContent(type="text", text=result_text)]

    async def _handle_execute(self, arguments: dict) -> list[TextContent]:
        """Handle execute tool call."""
        code = arguments.get("code")
        if not code:
            return [TextContent(type="text", text="Error: 'code' parameter required")]
        
        logger.info(f"Executing API call with code length: {len(code)}")
        
        # Get current token (auto-refreshes if expired)
        token = self.auth.get_token()
        
        result = await self.sandbox.run_execute(
            code=code,
            api_token=token,
        )
        
        # Format result as text
        result_text = json.dumps(result, indent=2)
        
        return [TextContent(type="text", text=result_text)]

    async def run(self):
        """Run the MCP server."""
        logger.info("Starting CentralMind MCP server...")
        logger.info(f"Spec path: {self.spec_path}")
        logger.info(f"Deno path: {self.config.deno_path}")
        logger.info(f"API host: {self.auth.host}")
        
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )
