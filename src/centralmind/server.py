"""MCP server implementation with search and execute tools.

Supports multiple client environments ("clients") sharing one running server.
Each MCP connection (session) has its own "active client" selection made via
the `switch_client` tool — platform credentials and Deno sandboxes are built
lazily per (client, platform) pair and cached, so adding a new client is
just a credential-store write, not a server restart.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .clients_store import ClientsStore
from .config import ServerConfig
from .platform_factory import PLATFORM_SPECS, build_platform_auth, resolve_api_mode
from .sandbox import DenoSandbox
from .spec_indexer import generate_index_from_file

logger = logging.getLogger(__name__)

# Session key used when the request context is unavailable (should not
# normally happen — both stdio and streamable-http set it per connection).
_FALLBACK_SESSION_KEY = "_no_request_context_"

manual_analysis_prompt = (
    "You are an expert network operations analyst performing deep operational analysis. "
    "Provide rich, actionable insight on this result. Think broadly about operational impact, "
    "dependencies, risks, and next steps. You may make additional targeted calls using the "
    "client.request(...) pattern if you need more context."
)


class CentralMindServer:
    """CentralMind MCP server with Code Mode pattern, multi-client aware."""

    manual_analysis_prompt = manual_analysis_prompt

    def __init__(
        self,
        config: ServerConfig,
        clients_store: ClientsStore,
        resolved_spec_paths: Dict[str, str],
    ):
        """Initialize server with global config, the client credential store,
        and a platform -> resolved OpenAPI spec path mapping (platform-wide,
        independent of any client)."""
        self.config = config
        self.clients_store = clients_store
        self.resolved_spec_paths = resolved_spec_paths
        self.server = Server("centralmind")

        self.obfuscated = getattr(self.config, "centralmind_obfuscate_api", False)
        if self.obfuscated:
            logger.warning(
                "⚠️  Runtime API Obfuscation ENABLED — "
                "the LLM will see fictional resource names."
            )

        # Caches. Spec index/paths are platform-wide (no client dependency).
        # Auth+sandbox bundles are per (client_id, platform) since they hold
        # credentials and a per-platform rate limiter.
        self._spec_path_cache: Dict[str, Path] = {}
        self._spec_index_cache: Dict[str, str] = {}
        self._bundle_cache: Dict[tuple, Dict[str, Any]] = {}

        # Per-session "active client" selection.
        self._active_client_by_session: Dict[Any, str] = {}

        self._register_handlers()

    # ------------------------------------------------------------------
    # Session / active-client resolution
    # ------------------------------------------------------------------

    def _session_key(self) -> Any:
        """A key stable for the lifetime of one MCP connection, distinct
        across concurrent connections (each streamable-http connection runs
        `Server.run()` in its own task, so the underlying contextvar is
        naturally isolated per session)."""
        try:
            return self.server.request_context.session
        except LookupError:
            return _FALLBACK_SESSION_KEY

    def _get_active_client_id(self) -> str:
        key = self._session_key()
        if key not in self._active_client_by_session:
            default_id = self.clients_store.get_default_id()
            if default_id is None:
                raise ValueError(
                    "No clients configured yet. Run `centralmind admin` to add one."
                )
            self._active_client_by_session[key] = default_id
        return self._active_client_by_session[key]

    # ------------------------------------------------------------------
    # Platform-wide spec caching (shared across all clients)
    # ------------------------------------------------------------------

    def _get_spec_path(self, platform: str) -> Optional[Path]:
        if platform not in self.resolved_spec_paths:
            return None
        if platform in self._spec_path_cache:
            return self._spec_path_cache[platform]

        spec_path = Path(self.resolved_spec_paths[platform])
        if self.obfuscated:
            from .obfuscator import obfuscate_spec_file
            spec_path = obfuscate_spec_file(spec_path)

        self._spec_path_cache[platform] = spec_path
        return spec_path

    def _get_spec_index(self, platform: str) -> Optional[str]:
        if platform in self._spec_index_cache:
            return self._spec_index_cache[platform]

        spec_path = self._get_spec_path(platform)
        if spec_path is None:
            return None

        logger.info(f"Generating {platform} spec index...")
        index = generate_index_from_file(str(spec_path), force_search_first=self.obfuscated)
        self._spec_index_cache[platform] = index
        return index

    # ------------------------------------------------------------------
    # Per-(client, platform) auth + sandbox bundle, built lazily on first use
    # ------------------------------------------------------------------

    def _get_bundle(self, client_id: str, platform: str) -> Dict[str, Any]:
        profile = self.clients_store.get(client_id)
        if profile is None:
            raise ValueError(f"Unknown client_id: {client_id}")

        # Cache key includes updated_at so editing a client (credentials,
        # api_mode, etc.) via the admin UI while the server is running
        # invalidates any bundle already built for it, instead of silently
        # keeping stale auth/permissions until a restart.
        cache_key = (client_id, platform, profile.updated_at)
        if cache_key in self._bundle_cache:
            return self._bundle_cache[cache_key]

        spec_path = self._get_spec_path(platform)
        if spec_path is None:
            raise ValueError(f"No resolved OpenAPI spec available for platform '{platform}'")

        auth = build_platform_auth(platform, profile)
        if auth is None:
            raise ValueError(
                f"Client '{profile.name}' has no credentials configured for '{platform}'. "
                "Use `centralmind admin` to add them, or `list_clients` to see what's available."
            )

        spec = PLATFORM_SPECS[platform]
        verify_ssl = getattr(profile, spec.verify_ssl_field) if spec.verify_ssl_field else True
        effective_api_mode = resolve_api_mode(self.config.centralmind_api_mode, profile.api_mode)

        sandbox = DenoSandbox(
            deno_path=self.config.deno_path,
            api_host=auth.host,
            timeout=30,
            api_mode=effective_api_mode,
            rate_limit=self.config.centralmind_rate_limit,
            max_concurrent=self.config.centralmind_max_concurrent,
            obfuscated=self.obfuscated,
            verify_ssl=verify_ssl,
            client_name=platform,
            auth_scheme=spec.auth_scheme,
            base_url=getattr(auth, "base_url", None),
        )

        bundle = {
            "auth": auth,
            "spec_path": spec_path,
            "sandbox": sandbox,
            "extra_params": spec.extra_params or {},
            "required_params": spec.required_params or [],
        }
        self._bundle_cache[cache_key] = bundle
        return bundle

    # ------------------------------------------------------------------
    # MCP handlers
    # ------------------------------------------------------------------

    def _register_handlers(self):
        """Register MCP tool handlers."""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """List available tools: global client-management tools, plus
            search/execute pairs for whichever platforms the session's
            currently active client has credentials for."""
            tools = [
                Tool(
                    name="list_clients",
                    description=(
                        "List configured clients, the platforms each has credentials for, "
                        "and which client is currently active for this session."
                    ),
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="switch_client",
                    description=(
                        "Switch the active client for this session. All subsequent search_*/execute_* "
                        "calls target the newly selected client's credentials until switched again. "
                        "Call `list_clients` first to see valid client_id values."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "client_id": {"type": "string", "description": "The client id to switch to"}
                        },
                        "required": ["client_id"],
                    },
                ),
            ]

            try:
                active_id = self._get_active_client_id()
            except ValueError:
                return tools  # no clients configured yet

            profile = self.clients_store.get(active_id)
            for platform in profile.configured_platforms():
                spec_index = self._get_spec_index(platform)
                if spec_index is None:
                    continue  # credentials exist but no resolved spec for this platform
                tools.extend(self._build_platform_tools(platform, spec_index))

            return tools

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Any) -> list[TextContent]:
            """Handle tool calls."""
            try:
                if name == "list_clients":
                    return await self._handle_list_clients()
                if name == "switch_client":
                    return await self._handle_switch_client(arguments)

                for platform in PLATFORM_SPECS:
                    if name == f"search_{platform}":
                        return await self._handle_search(platform, arguments)
                    elif name == f"execute_{platform}":
                        primary_result = await self._handle_execute(platform, arguments)
                        if getattr(self.config, "centralmind_enable_enrichment", True):
                            return await self._perform_enrichment(platform, primary_result)
                        return primary_result

                return [
                    TextContent(
                        type="text",
                        text=f"Unknown tool: {name}",
                    )
                ]
            except Exception as e:
                logger.error(f"Tool call error: {e}", exc_info=True)
                error_msg = str(e)
                # Scrub tokens from exception messages across every cached bundle
                for bundle in self._bundle_cache.values():
                    current_token = getattr(bundle["auth"], "_access_token", None)
                    if current_token:
                        error_msg = error_msg.replace(current_token, "[REDACTED]")
                return [
                    TextContent(
                        type="text",
                        text=f"Error: {error_msg}",
                    )
                ]

    def _build_platform_tools(self, platform: str, spec_index: str) -> list[Tool]:
        """Build the search_<platform>/execute_<platform> Tool schemas."""
        spec = PLATFORM_SPECS[platform]

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

        search_properties = {"code": {"type": "string", "description": search_desc}}
        execute_properties = {"code": {"type": "string", "description": execute_example}}
        execute_required = ["code"]

        if spec.extra_params:
            execute_properties.update(spec.extra_params)
        if spec.required_params:
            execute_required.extend(spec.required_params)

        return [
            Tool(
                name=f"search_{platform}",
                description=spec_index,
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
        ]

    async def _handle_list_clients(self) -> list[TextContent]:
        """Handle list_clients tool call."""
        profiles = self.clients_store.list()
        try:
            active_id = self._get_active_client_id()
        except ValueError:
            active_id = None
        default_id = self.clients_store.get_default_id()

        result = [
            {
                "id": p.id,
                "name": p.name,
                "platforms": p.configured_platforms(),
                "api_mode": resolve_api_mode(self.config.centralmind_api_mode, p.api_mode),
                "active": p.id == active_id,
                "default": p.id == default_id,
            }
            for p in profiles
        ]
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_switch_client(self, arguments: dict) -> list[TextContent]:
        """Handle switch_client tool call."""
        client_id = arguments.get("client_id")
        if not client_id:
            return [TextContent(type="text", text="Error: 'client_id' parameter required")]

        profile = self.clients_store.get(client_id)
        if profile is None:
            return [
                TextContent(
                    type="text",
                    text=f"Error: unknown client_id '{client_id}'. Call list_clients to see available clients.",
                )
            ]

        self._active_client_by_session[self._session_key()] = client_id
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "active_client": profile.id,
                        "name": profile.name,
                        "platforms": profile.configured_platforms(),
                    },
                    indent=2,
                ),
            )
        ]

    async def _handle_search(self, platform: str, arguments: dict) -> list[TextContent]:
        """Handle search tool call."""
        code = arguments.get("code")
        if not code:
            return [TextContent(type="text", text="Error: 'code' parameter required")]

        data = self._get_bundle(self._get_active_client_id(), platform)

        logger.info(f"Executing {platform} search with code length: {len(code)}")

        result = await data["sandbox"].run_search(
            code=code,
            spec_path=str(data["spec_path"]),
        )

        result_text = json.dumps(result, indent=2)

        return [TextContent(type="text", text=result_text)]

    async def _handle_execute(self, platform: str, arguments: dict) -> list[TextContent]:
        """Handle execute tool call."""
        code = arguments.get("code")
        if not code:
            return [TextContent(type="text", text="Error: 'code' parameter required")]

        data = self._get_bundle(self._get_active_client_id(), platform)

        logger.info(f"Executing {platform} API call with code length: {len(code)}")

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

        result_text = json.dumps(result, indent=2)

        return [TextContent(type="text", text=result_text)]

    # ------------------------------------------------------------------
    # Dynamic Enrichment — a best-effort, heuristics-based post-execute
    # analysis pass (offline/error signals, blast radius, recommendations).
    # Platform-agnostic: operates purely on the JSON result, no credentials
    # or client/platform state involved. Gated by centralmind_enable_enrichment.
    # ------------------------------------------------------------------

    def _detect_anomalies(self, data: Any):
        """Iteratively inspect data for offline/down devices and errors."""
        offline_count = 0
        errors_found = []

        offline_keywords = {"offline", "down", "disconnected", "critical", "failed"}
        error_keywords = {"error", "unauthorized"}

        stack = [data]

        while stack:
            curr = stack.pop()
            if isinstance(curr, dict):
                device_offline = False

                # Non-JSON error detection: scan raw_output if present
                if "raw_output" in curr:
                    raw_val = curr["raw_output"]
                    if isinstance(raw_val, str):
                        raw_val_lower = raw_val.lower()
                        for err_kw in error_keywords:
                            if err_kw in raw_val_lower:
                                errors_found.append(f"raw_output contains error keyword: {err_kw}")

                for k, v in curr.items():
                    k_lower = str(k).lower()

                    # Check keys for error/unauthorized
                    for err_kw in error_keywords:
                        if err_kw in k_lower:
                            # Skip flagging keys containing "error" or "unauthorized" as errors if their value v is in (None, False, 0, "", [], {})
                            if v in (None, False, 0, "", [], {}):
                                continue

                            if isinstance(v, (dict, list, tuple, set)):
                                v_str = f"<{type(v).__name__} of length {len(v)}>"
                            else:
                                v_str = str(v)
                            if len(v_str) > 200:
                                v_str = v_str[:200]
                            errors_found.append(f"Key '{k}' contains anomaly keyword: {v_str}")

                    if k_lower in ("status", "state", "device_status", "status_code", "power_status", "oper_state"):
                        if isinstance(v, str):
                            v_lower = v.lower()
                            if v_lower in offline_keywords:
                                device_offline = True

                    if k_lower in ("online", "connected"):
                        if v == False or v == 0 or (isinstance(v, str) and v.lower() in {"offline", "disconnected", "down", "false"}):
                            device_offline = True

                if device_offline:
                    offline_count += 1

                # Push child containers to stack for iterative traversal
                for v in curr.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)

            elif isinstance(curr, list):
                for item in curr:
                    if isinstance(item, (dict, list)):
                        stack.append(item)

        return offline_count, errors_found

    async def _perform_enrichment(self, platform: str, primary_result: list[TextContent]) -> list[TextContent]:
        """Perform heuristics-based enrichment analysis pass on execute result."""
        if not primary_result or not isinstance(primary_result, list) or primary_result[0].text is None:
            return primary_result

        try:
            raw_text = primary_result[0].text
            # Try parsing the primary result as JSON
            try:
                data = json.loads(raw_text)
            except Exception:
                # If not valid JSON, wrap it in a dict
                data = {"raw_output": raw_text}

            # Run heuristics to detect offline devices and errors
            offline_count, errors_found = self._detect_anomalies(data)

            # Determine blast radius and client impact
            if offline_count > 0 or errors_found:
                if offline_count == 1:
                    blast_radius = "Medium"
                elif 1 < offline_count <= 5:
                    blast_radius = "High"
                elif offline_count > 5:
                    blast_radius = "Critical"
                else:  # Only errors found, no offline devices
                    blast_radius = "Medium"

                client_count = offline_count * 15
                client_impact = {
                    "count": client_count,
                    "description": f"Potential service disruption affecting approximately {client_count} clients due to {offline_count} offline/down devices."
                }

                # Structured recommendations and risks
                risks = []
                recommendations = []
                if offline_count > 0:
                    risks.extend([
                        f"Loss of network connectivity for clients connected to the {offline_count} offline devices.",
                        "Degraded wireless/wired coverage in the physical areas serviced by these devices.",
                        "Potential cascading failures if these devices are critical infrastructure (gateways/switches)."
                    ])
                    recommendations.extend([
                        "Initiate a ping test to the offline devices to verify IP reachability.",
                        "Verify physical connectivity (cables, switch ports) and power status (PoE budget, power cycle).",
                        "Check device logs and console output for crash information or boot loops.",
                        "Review recent configuration changes or firmware updates applied to these devices."
                    ])
                if errors_found:
                    risks.append("API request error, failure, or unauthorized access preventing successful management operations.")
                    recommendations.append("Verify API credentials, permissions, and network path to the API endpoint.")

                correlations = []
                if offline_count > 0:
                    correlations.append(f"Multiple offline events detected across {offline_count} devices, suggesting potential power or upstream switch issues.")
                if errors_found:
                    correlations.append("API communication errors correlated with authorization/access key issues.")

                enrichment = {
                    "impact_summary": f"Detected {offline_count} offline devices and {len(errors_found)} errors. Action required.",
                    "blast_radius": blast_radius,
                    "client_impact": client_impact,
                    "correlations": correlations,
                    "risks": risks,
                    "recommendations": recommendations,
                    "manual_analysis_prompt": self.manual_analysis_prompt,
                }
            else:
                # Healthy system
                enrichment = {
                    "impact_summary": "System appears healthy with no offline devices or operational errors detected.",
                    "blast_radius": "Low",
                    "client_impact": {
                        "count": 0,
                        "description": "No operational client impact detected."
                    },
                    "correlations": [],
                    "risks": ["Standard low-risk operations. No anomalies detected."],
                    "recommendations": ["No recommendations needed. Standard monitoring continues."],
                    "manual_analysis_prompt": self.manual_analysis_prompt,
                }

            # Append _enrichment to data
            data["_enrichment"] = enrichment

            # Format and return updated TextContent
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        except Exception as e:
            logger.warning(f"Enrichment pass failed: {e}")
            # If enrichment fails, we return the original result to not block tool execution
            return primary_result

    # ------------------------------------------------------------------
    # Transports
    # ------------------------------------------------------------------

    async def run_stdio(self):
        """Run the MCP server over stdio (local subprocess transport)."""
        logger.info("Starting CentralMind MCP server (stdio transport)...")
        logger.info(f"Deno path: {self.config.deno_path}")
        logger.info(f"Configured clients: {[p.name for p in self.clients_store.list()]}")

        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )

    async def run_http(
        self,
        host: str,
        port: int,
        api_key: str,
        ssl_certfile: Optional[str] = None,
        ssl_keyfile: Optional[str] = None,
    ):
        """Run the MCP server over Streamable HTTP, reachable on the network.

        Every request must carry `Authorization: Bearer <api_key>`. Sessions
        are stateful (one server task per connection) so that each session's
        `switch_client` choice persists across its own subsequent calls
        without leaking into other concurrent sessions.

        When ssl_certfile/ssl_keyfile are given, uvicorn terminates TLS
        directly (self-signed by default, or an imported CA-issued pair —
        see tls.py); omit both to serve plain HTTP (e.g. behind a reverse
        proxy that already terminates TLS).
        """
        import contextlib
        import socket

        import uvicorn
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.routing import Mount

        from .auth_middleware import BearerTokenMiddleware

        session_manager = StreamableHTTPSessionManager(app=self.server, stateless=False)

        async def handle_mcp(scope, receive, send):
            await session_manager.handle_request(scope, receive, send)

        @contextlib.asynccontextmanager
        async def lifespan(app):
            async with session_manager.run():
                yield

        app = Starlette(
            middleware=[Middleware(BearerTokenMiddleware, api_key=api_key)],
            routes=[Mount("/mcp", app=handle_mcp)],
            lifespan=lifespan,
        )

        logger.info("Starting CentralMind MCP server (streamable-http transport)...")
        logger.info(f"Configured clients: {[p.name for p in self.clients_store.list()]}")

        display_host = host
        if host == "0.0.0.0":
            try:
                display_host = socket.gethostbyname(socket.gethostname())
            except OSError:
                display_host = "<this-machine-ip>"

        scheme = "https" if ssl_certfile else "http"
        # Note the trailing slash: Starlette 307-redirects bare "/mcp" hits to
        # "/mcp/" (Mount's default redirect_slashes behavior), and not every
        # MCP client follows redirects reliably on POST. Give out the URL
        # that works without a redirect.
        logger.info(f"MCP endpoint: {scheme}://{display_host}:{port}/mcp/")
        logger.info(f"API key (send as 'Authorization: Bearer <key>'): {api_key}")
        logger.info("Anyone with this key and network access to this host can use the configured tools — treat it like a password.")
        if ssl_certfile:
            from .tls import cert_info
            info = cert_info(Path(ssl_certfile))
            if info and info["self_signed"]:
                logger.warning(
                    "Serving HTTPS with a self-signed certificate — MCP clients will need to "
                    "explicitly trust it (or ignore the warning). Run `centralmind tls import` "
                    "to install a certificate from an enterprise or public CA instead."
                )
        else:
            logger.warning(
                "Serving PLAIN HTTP — the API key is the only protection on the wire. "
                "Use TLS (default) unless a reverse proxy is already terminating it."
            )

        uvicorn_config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="debug" if self.config.centralmind_debug else "warning",
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
        )
        uvicorn_server = uvicorn.Server(uvicorn_config)
        await uvicorn_server.serve()
