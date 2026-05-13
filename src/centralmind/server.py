"""MCP server implementation with search and execute tools."""

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
    """CentralMind MCP server with Code Mode pattern."""

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
        """Initialize server with config, auth managers, and resolved spec paths."""
        self.config = config
        self.server = Server("centralmind")
        
        self.platforms: Dict[str, Dict[str, Any]] = {}

        # Apply runtime obfuscation if configured
        self.obfuscated = getattr(self.config, "centralmind_obfuscate_api", False)
        if self.obfuscated:
            logger.warning(
                "⚠️  Runtime API Obfuscation ENABLED — "
                "the LLM will see fictional resource names."
            )

        if central_auth and central_spec_path:
            spec_path = Path(central_spec_path)
            if self.obfuscated:
                from .obfuscator import obfuscate_spec_file
                spec_path = obfuscate_spec_file(spec_path)
            
            logger.info("Generating central spec index...")
            spec_index = generate_index_from_file(
                str(spec_path), force_search_first=self.obfuscated
            )
            
            self.platforms["central"] = {
                "auth": central_auth,
                "spec_path": spec_path,
                "spec_index": spec_index,
                "sandbox": DenoSandbox(
                    deno_path=config.deno_path,
                    api_host=central_auth.host,
                    timeout=30,
                    api_mode=config.centralmind_api_mode,
                    rate_limit=config.centralmind_rate_limit,
                    max_concurrent=config.centralmind_max_concurrent,
                    obfuscated=self.obfuscated,
                    client_name="central",
                    auth_scheme="Bearer",
                )
            }

        if clearpass_auth and clearpass_spec_path:
            spec_path = Path(clearpass_spec_path)
            if self.obfuscated:
                from .obfuscator import obfuscate_spec_file
                spec_path = obfuscate_spec_file(spec_path)
            
            logger.info("Generating clearpass spec index...")
            spec_index = generate_index_from_file(
                str(spec_path), force_search_first=self.obfuscated
            )
            
            self.platforms["clearpass"] = {
                "auth": clearpass_auth,
                "spec_path": spec_path,
                "spec_index": spec_index,
                "sandbox": DenoSandbox(
                    deno_path=config.deno_path,
                    api_host=clearpass_auth.host,
                    timeout=30,
                    api_mode=config.centralmind_api_mode,
                    rate_limit=config.centralmind_rate_limit,
                    max_concurrent=config.centralmind_max_concurrent,
                    obfuscated=self.obfuscated,
                    verify_ssl=config.clearpass_verify_ssl,
                    client_name="clearpass",
                    auth_scheme="Bearer",
                    base_url=getattr(clearpass_auth, "base_url", None),
                )
            }

        if mist_auth and mist_spec_path:
            spec_path = Path(mist_spec_path)
            if self.obfuscated:
                from .obfuscator import obfuscate_spec_file
                spec_path = obfuscate_spec_file(spec_path)
            
            logger.info("Generating mist spec index...")
            spec_index = generate_index_from_file(
                str(spec_path), force_search_first=self.obfuscated
            )
            
            self.platforms["mist"] = {
                "auth": mist_auth,
                "spec_path": spec_path,
                "spec_index": spec_index,
                "sandbox": DenoSandbox(
                    deno_path=config.deno_path,
                    api_host=mist_auth.host,
                    timeout=30,
                    api_mode=config.centralmind_api_mode,
                    rate_limit=config.centralmind_rate_limit,
                    max_concurrent=config.centralmind_max_concurrent,
                    obfuscated=self.obfuscated,
                    client_name="mist",
                    auth_scheme="Token",
                )
            }

        if axis_auth and axis_spec_path:
            spec_path = Path(axis_spec_path)
            if self.obfuscated:
                from .obfuscator import obfuscate_spec_file
                spec_path = obfuscate_spec_file(spec_path)
            
            logger.info("Generating axis spec index...")
            spec_index = generate_index_from_file(
                str(spec_path), force_search_first=self.obfuscated
            )
            
            self.platforms["axis"] = {
                "auth": axis_auth,
                "spec_path": spec_path,
                "spec_index": spec_index,
                "sandbox": DenoSandbox(
                    deno_path=config.deno_path,
                    api_host=axis_auth.host,
                    timeout=30,
                    api_mode=config.centralmind_api_mode,
                    rate_limit=config.centralmind_rate_limit,
                    max_concurrent=config.centralmind_max_concurrent,
                    obfuscated=self.obfuscated,
                    client_name="axis",
                    auth_scheme="Bearer",
                )
            }

        if sdc_auth and sdc_spec_path:
            spec_path = Path(sdc_spec_path)
            if self.obfuscated:
                from .obfuscator import obfuscate_spec_file
                spec_path = obfuscate_spec_file(spec_path)
            
            logger.info("Generating sdc spec index...")
            spec_index = generate_index_from_file(
                str(spec_path), force_search_first=self.obfuscated
            )
            
            self.platforms["sdc"] = {
                "auth": sdc_auth,
                "spec_path": spec_path,
                "spec_index": spec_index,
                "sandbox": DenoSandbox(
                    deno_path=config.deno_path,
                    api_host=sdc_auth.host,
                    timeout=30,
                    api_mode=config.centralmind_api_mode,
                    rate_limit=config.centralmind_rate_limit,
                    max_concurrent=config.centralmind_max_concurrent,
                    obfuscated=self.obfuscated,
                    client_name="sdc",
                    auth_scheme="x-api-key",
                )
            }

        if uxi_auth and uxi_spec_path:
            spec_path = Path(uxi_spec_path)
            if self.obfuscated:
                from .obfuscator import obfuscate_spec_file
                spec_path = obfuscate_spec_file(spec_path)
            
            logger.info("Generating uxi spec index...")
            spec_index = generate_index_from_file(
                str(spec_path), force_search_first=self.obfuscated
            )
            
            self.platforms["uxi"] = {
                "auth": uxi_auth,
                "spec_path": spec_path,
                "spec_index": spec_index,
                "sandbox": DenoSandbox(
                    deno_path=config.deno_path,
                    api_host=uxi_auth.host,
                    timeout=30,
                    api_mode=config.centralmind_api_mode,
                    rate_limit=config.centralmind_rate_limit,
                    max_concurrent=config.centralmind_max_concurrent,
                    obfuscated=self.obfuscated,
                    verify_ssl=config.uxi_verify_ssl,
                    client_name="uxi",
                    auth_scheme="Bearer",
                )
            }

        if aoscx_auth and aoscx_spec_path:
            spec_path = Path(aoscx_spec_path)
            if self.obfuscated:
                from .obfuscator import obfuscate_spec_file
                spec_path = obfuscate_spec_file(spec_path)
            
            logger.info("Generating aoscx spec index...")
            spec_index = generate_index_from_file(
                str(spec_path), force_search_first=self.obfuscated
            )
            
            self.platforms["aoscx"] = {
                "auth": aoscx_auth,
                "spec_path": spec_path,
                "spec_index": spec_index,
                "sandbox": DenoSandbox(
                    deno_path=config.deno_path,
                    api_host=aoscx_auth.host, # This is "*"
                    timeout=30,
                    api_mode=config.centralmind_api_mode,
                    rate_limit=config.centralmind_rate_limit,
                    max_concurrent=config.centralmind_max_concurrent,
                    obfuscated=self.obfuscated,
                    verify_ssl=config.aoscx_verify_ssl,
                    client_name="aoscx",
                    auth_scheme="aoscx-cookie",
                ),
                "extra_params": {
                    "switch_ip": {
                        "type": "string",
                        "description": "IP address or hostname of the AOS-CX switch"
                    },
                    "version": {
                        "type": "string",
                        "description": "API version to use (e.g., v10.13)",
                        "default": "v10.13"
                    }
                },
                "required_params": ["switch_ip"]
            }

        self._register_handlers()

    def _register_handlers(self):
        """Register MCP tool handlers."""
        
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """List available tools."""
            tools = []
            
            for platform, data in self.platforms.items():
                if self.obfuscated:
                    search_desc = (
                        f"JavaScript async arrow function to search the {platform.capitalize()} OpenAPI spec. "
                        "IMPORTANT: To save context during initial discovery, NEVER return full parameter or schema objects. "
                        "Return ONLY an array of {method, path, name}. "
                        "Because some specs lack summaries, fallback to operationId or path segments for the name.\n"
                        "Example: async () => { const results = []; for (const [path, methods] "
                        "of Object.entries(spec.paths)) { for (const [method, op] of "
                        "Object.entries(methods)) { if (op.tags?.some(t => "
                        't.toLowerCase().includes("wireless"))) { '
                        "const name = op.summary || op.operationId || path.split('/').pop(); "
                        "results.push({method: method.toUpperCase(), path, name}); } } } return results; }"
                    )
                    execute_desc = (
                        f"Execute JS against the {platform.capitalize()} API. Use {platform}.request({{method, path, body, params}}).\n"
                        "IMPORTANT: You MUST use the `search` tool first to find exact paths and "
                        "parameters — your pre-trained knowledge of this API will not apply.\n"
                        "method defaults to GET. Chain multiple calls, filter/transform results in JS.\n"
                        f"{platform}.allowedMethods shows permitted HTTP methods.\n"
                        "For paginated results: check if total > results.length, loop with page/start params."
                    )
                    execute_example = (
                        "JavaScript async arrow function to execute. "
                        "Paths must include their full prefix from the spec. "
                        f'Example: async () => {{ const result = await {platform}.request({{path: "/network-monitoring/v1/aps", params: {{limit: 5}}}}); '
                        "return result; }"
                    )
                else:
                    search_desc = (
                        f"JavaScript async arrow function to search the {platform.capitalize()} OpenAPI spec. "
                        "IMPORTANT: The `spec` object is already loaded in the environment. DO NOT try to read "
                        "the JSON files from disk using cat or python. ONLY use this tool to discover paths and parameters.\n"
                        "IMPORTANT: To save context during initial discovery, NEVER return full parameter or schema objects. "
                        "Return ONLY an array of {method, path, name}. "
                        "Because some specs lack summaries, fallback to operationId or path segments for the name.\n"
                        "Example: async () => { const results = []; for (const [path, methods] "
                        "of Object.entries(spec.paths)) { for (const [method, op] of "
                        "Object.entries(methods)) { if (op.tags?.some(t => "
                        't.toLowerCase().includes("wlan"))) { '
                        "const name = op.summary || op.operationId || path.split('/').pop(); "
                        "results.push({method: method.toUpperCase(), path, name}); } } } return results; }"
                    )
                    execute_desc = (
                        f"Execute JS against the {platform.capitalize()} API. Use {platform}.request({{method, path, body, params}}).\n"
                        "method defaults to GET. Chain multiple calls, filter/transform results in JS.\n"
                        f"{platform}.allowedMethods shows permitted HTTP methods.\n"
                        "For paginated results: check if total > results.length, loop with page/start params.\n"
                        "For write ops: return a preview first, execute write only after user confirms."
                    )
                    execute_example = (
                        "JavaScript async arrow function to execute. "
                        "Paths must include their full prefix from the spec. "
                        f'Example: async () => {{ const result = await {platform}.request({{path: "/network-monitoring/v1/aps", params: {{limit: 5}}}}); '
                        "return result; }"
                    )

                # Build tool schemas, merging any platform-specific extra parameters
                search_properties = {
                    "code": {
                        "type": "string",
                        "description": search_desc,
                    }
                }
                execute_properties = {
                    "code": {
                        "type": "string",
                        "description": execute_example,
                    }
                }
                execute_required = ["code"]
                
                if "extra_params" in data:
                    execute_properties.update(data["extra_params"])
                if "required_params" in data:
                    execute_required.extend(data["required_params"])

                tools.extend([
                    Tool(
                        name=f"search_{platform}",
                        description=data["spec_index"],
                        inputSchema={
                            "type": "object",
                            "properties": search_properties,
                            "required": ["code"],
                        },
                    ),
                    Tool(
                        name=f"execute_{platform}",
                        description=execute_desc,
                        inputSchema={
                            "type": "object",
                            "properties": execute_properties,
                            "required": execute_required,
                        },
                    ),
                ])
                
            return tools

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Any) -> list[TextContent]:
            """Handle tool calls."""
            try:
                for platform, data in self.platforms.items():
                    if name == f"search_{platform}":
                        return await self._handle_search(platform, arguments)
                    elif name == f"execute_{platform}":
                        return await self._handle_execute(platform, arguments)
                        
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
                for platform, data in self.platforms.items():
                    current_token = getattr(data["auth"], "_access_token", None)
                    if current_token:
                        error_msg = error_msg.replace(current_token, "[REDACTED]")
                return [
                    TextContent(
                        type="text",
                        text=f"Error: {error_msg}",
                    )
                ]

    async def _handle_search(self, platform: str, arguments: dict) -> list[TextContent]:
        """Handle search tool call."""
        code = arguments.get("code")
        if not code:
            return [TextContent(type="text", text="Error: 'code' parameter required")]
        
        logger.info(f"Executing {platform} search with code length: {len(code)}")
        
        data = self.platforms[platform]
        result = await data["sandbox"].run_search(
            code=code,
            spec_path=str(data["spec_path"]),
        )
        
        # Format result as text
        result_text = json.dumps(result, indent=2)
        
        return [TextContent(type="text", text=result_text)]

    async def _handle_execute(self, platform: str, arguments: dict) -> list[TextContent]:
        """Handle execute tool call."""
        code = arguments.get("code")
        if not code:
            return [TextContent(type="text", text="Error: 'code' parameter required")]
        
        logger.info(f"Executing {platform} API call with code length: {len(code)}")
        
        data = self.platforms[platform]
        
        # Determine token and execution parameters
        if platform == "aoscx":
            switch_ip = arguments.get("switch_ip")
            version = arguments.get("version", "v10.13")
            if not switch_ip:
                return [TextContent(type="text", text="Error: 'switch_ip' parameter required for AOS-CX")]
            
            token = data["auth"].get_token(switch_ip, version)
            # Override host and base_url for dynamic switch targeting
            result = await data["sandbox"].run_execute(
                code=code,
                api_token=token,
                api_host=switch_ip,
                base_url=f"https://{switch_ip}/rest/{version}" if "://" not in switch_ip else f"{switch_ip}/rest/{version}"
            )
        else:
            # Get current token (auto-refreshes if expired)
            token = data["auth"].get_token()
            result = await data["sandbox"].run_execute(
                code=code,
                api_token=token,
            )
        
        # Format result as text
        result_text = json.dumps(result, indent=2)
        
        return [TextContent(type="text", text=result_text)]

    async def run(self):
        """Run the MCP server."""
        logger.info("Starting CentralMind MCP server...")
        for platform, data in self.platforms.items():
            logger.info(f"Platform: {platform}")
            logger.info(f"  Spec path: {data['spec_path']}")
            logger.info(f"  API host: {data['auth'].host}")
        logger.info(f"Deno path: {self.config.deno_path}")
        
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )
