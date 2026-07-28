"""CLI entry point for CentralMind MCP server."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

from dotenv import load_dotenv

from . import __version__
from .auth import AoscxAuth, AxisAuth, CentralAuth, ClearpassAuth, MistAuth, SdcAuth, UxiAuth
from .config import ServerConfig
from .server import CentralMindServer


def setup_logging(debug: bool = False):
    """Configure logging."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def main(args: argparse.Namespace):
    """Main async entry point."""
    # Load env file if specified
    if args.env_file:
        env_path = Path(args.env_file)
        if not env_path.exists():
            print(f"Error: env file not found: {args.env_file}", file=sys.stderr)
            sys.exit(1)
        load_dotenv(env_path)
        config = ServerConfig()
    else:
        # Load from default .env or environment
        load_dotenv()
        config = ServerConfig()
    
    # Override debug setting from CLI
    if args.debug:
        config.centralmind_debug = True
    
    setup_logging(config.centralmind_debug)
    
    central_auth = None
    if config.central_client_id and config.central_client_secret:
        try:
            central_auth = CentralAuth(
                client_id=config.central_client_id,
                client_secret=config.central_client_secret,
                base_url=config.central_base_url,
            )
        except RuntimeError as e:
            print(f"Warning: Central authentication failed: {e}", file=sys.stderr)

    clearpass_auth = None
    if config.clearpass_client_id and config.clearpass_client_secret:
        try:
            clearpass_auth = ClearpassAuth(
                client_id=config.clearpass_client_id,
                client_secret=config.clearpass_client_secret,
                base_url=config.clearpass_base_url,
                verify_ssl=config.clearpass_verify_ssl,
            )
        except RuntimeError as e:
            print(f"Warning: ClearPass authentication failed: {e}", file=sys.stderr)

    mist_auth = None
    if config.mist_apitoken:
        mist_auth = MistAuth(
            api_token=config.mist_apitoken,
            host=config.mist_host,
        )

    axis_auth = None
    if config.axis_apitoken:
        axis_auth = AxisAuth(
            api_token=config.axis_apitoken,
            host=config.axis_host,
        )

    sdc_auth = None
    if config.sdc_apitoken:
        sdc_auth = SdcAuth(
            api_token=config.sdc_apitoken,
            host=config.sdc_host,
        )

    uxi_auth = None
    if config.uxi_client_id and config.uxi_client_secret:
        try:
            uxi_auth = UxiAuth(
                client_id=config.uxi_client_id,
                client_secret=config.uxi_client_secret,
                host=config.uxi_host,
                verify_ssl=config.uxi_verify_ssl,
            )
        except RuntimeError as e:
            print(f"Warning: UXI authentication failed: {e}", file=sys.stderr)

    aoscx_auth = None
    if config.aoscx_username and config.aoscx_password:
        aoscx_auth = AoscxAuth(
            username=config.aoscx_username,
            password=config.aoscx_password,
            verify_ssl=config.aoscx_verify_ssl,
        )

    if not any([central_auth, clearpass_auth, mist_auth, sdc_auth, uxi_auth, aoscx_auth]):
        print("Error: No valid authentication credentials provided for Central, ClearPass, Mist, SDC, UXI, or AOS-CX.", file=sys.stderr)
        sys.exit(1)

    # ================================================================
    # Dynamic spec resolution: resolve any *.json that doesn't have
    # a matching *.resolved.json yet
    # ================================================================
    project_root = Path(__file__).parent.parent.parent
    spec_dir = project_root / "spec"

    resolved_spec_paths = {}

    if spec_dir.exists():
        for spec_file in sorted(spec_dir.glob("*.json")):
            if ".resolved." in spec_file.name:
                continue  # already a resolved file

            resolved_file = spec_dir / f"{spec_file.stem}.resolved.json"

            if not resolved_file.exists() or spec_file.stat().st_mtime > resolved_file.stat().st_mtime:
                logger.info(f"Auto-resolving {spec_file.name} -> {resolved_file.name}")
                try:
                    from .spec_resolver import resolve_spec
                    resolve_spec(str(spec_file), str(resolved_file))
                    logger.info(f"Successfully resolved {spec_file.name}")
                except Exception as e:
                    logger.warning(f"Failed to auto-resolve {spec_file.name}: {e}")

            if resolved_file.exists():
                resolved_spec_paths[spec_file.stem] = str(resolved_file)

    # Map resolved specs to platforms (simple heuristic based on filename)
    central_spec_path = resolved_spec_paths.get("openAPI") or resolved_spec_paths.get("platform")
    clearpass_spec_path = resolved_spec_paths.get("clearpass-openapi")
    mist_spec_path = resolved_spec_paths.get("mist")
    axis_spec_path = resolved_spec_paths.get("axis")
    sdc_spec_path = resolved_spec_paths.get("sdc")
    uxi_spec_path = resolved_spec_paths.get("uxi")
    aoscx_spec_path = resolved_spec_paths.get("aoscx")

    # CENTRALMIND_SPEC_PATH overrides the auto-resolved Central spec when set.
    if config.centralmind_spec_path:
        override = Path(config.centralmind_spec_path)
        if not override.exists():
            print(f"Error: Resolved central spec not found at {override}", file=sys.stderr)
            sys.exit(1)
        central_spec_path = str(override)

    # Create and run server
    try:
        server = CentralMindServer(
            config=config,
            central_auth=central_auth,
            central_spec_path=central_spec_path,
            clearpass_auth=clearpass_auth,
            clearpass_spec_path=clearpass_spec_path,
            mist_auth=mist_auth,
            mist_spec_path=mist_spec_path,
            axis_auth=axis_auth,
            axis_spec_path=axis_spec_path,
            sdc_auth=sdc_auth,
            sdc_spec_path=sdc_spec_path,
            uxi_auth=uxi_auth,
            uxi_spec_path=uxi_spec_path,
            aoscx_auth=aoscx_auth,
            aoscx_spec_path=aoscx_spec_path,
        )
        await server.run()
    except KeyboardInterrupt:
        logging.info("Server stopped by user")
    except Exception as e:
        logging.error(f"Server error: {e}", exc_info=True)
        sys.exit(1)


def main_sync():
    """Synchronous entry point for setup.py console_scripts."""
    # ── Step 1: lightweight pre-parse to detect whether a subcommand
    #    was given.  This avoids the bug where `--env-file .env` (no
    #    subcommand) is swallowed by the subparser as a positional arg,
    #    producing "invalid choice: '/path/to/.env'".
    _SUBCOMMANDS = {"serve", "fetch-specs"}
    has_subcommand = any(tok in _SUBCOMMANDS for tok in sys.argv[1:])

    if not has_subcommand:
        # Legacy / backwards-compatible flat-arg invocation:
        #   python -m centralmind --env-file .env --debug
        # No subcommand → treat as "serve".
        parser_compat = argparse.ArgumentParser(
            description="CentralMind - Code Mode MCP Server for Aruba Central API",
        )
        parser_compat.add_argument(
            "--version", action="version", version=f"centralmind {__version__}",
        )
        parser_compat.add_argument(
            "--transport", choices=["stdio", "sse"], default="stdio",
        )
        parser_compat.add_argument("--host", default="127.0.0.1")
        parser_compat.add_argument("--port", type=int, default=8000)
        parser_compat.add_argument("--env-file", help="Path to .env file to load")
        parser_compat.add_argument("--debug", action="store_true")
        args = parser_compat.parse_args()

        if args.transport != "stdio":
            print("Error: Only stdio transport is currently supported", file=sys.stderr)
            sys.exit(1)
        asyncio.run(main(args))
        return

    # ── Step 2: full subcommand-aware parser ─────────────────────────
    parser = argparse.ArgumentParser(
        description="CentralMind - Code Mode MCP Server for Aruba Central API",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"centralmind {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    # ── serve ────────────────────────────────────────────────────────
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the MCP server (default when no subcommand is given).",
    )
    serve_parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport type (default: stdio)",
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for SSE transport (default: 127.0.0.1)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE transport (default: 8000)",
    )
    serve_parser.add_argument(
        "--env-file",
        help="Path to .env file to load",
    )
    serve_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    # ── fetch-specs ──────────────────────────────────────────────────
    fetch_parser = subparsers.add_parser(
        "fetch-specs",
        help="Fetch OpenAPI specs from Aruba's developer hub.",
    )
    fetch_parser.add_argument(
        "--central-only",
        action="store_true",
        help="Only fetch Central (MRT + Config) specs, skip platform specs.",
    )
    fetch_parser.add_argument(
        "--resolve",
        action="store_true",
        help="Also run the $ref resolver after fetching.",
    )
    fetch_parser.add_argument(
        "--spec-dir",
        type=Path,
        default=None,
        help="Output directory for spec files (default: <project_root>/spec/).",
    )
    fetch_parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Disable SSL certificate verification (corporate proxy workaround).",
    )
    fetch_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )

    args = parser.parse_args()

    if args.command == "serve":
        if args.transport != "stdio":
            print("Error: Only stdio transport is currently supported", file=sys.stderr)
            sys.exit(1)
        asyncio.run(main(args))

    elif args.command == "fetch-specs":
        from .spec_fetcher import _configure_ssl_verify, fetch_all_specs

        logging.basicConfig(
            level=logging.DEBUG if args.debug else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

        _configure_ssl_verify(no_verify=args.no_verify_ssl)

        spec_dir = args.spec_dir
        if spec_dir is None:
            spec_dir = Path(__file__).resolve().parent.parent.parent / "spec"

        try:
            summary = fetch_all_specs(
                spec_dir,
                central_only=args.central_only,
                resolve=args.resolve,
            )
        except Exception as e:
            logger.error(f"Spec fetch failed: {e}", exc_info=True)
            sys.exit(1)

        # Print summary
        print("\n" + "═" * 60)
        print("  Spec Fetch Summary")
        print("═" * 60)
        if summary.get("central"):
            c = summary["central"]
            print(f"  Central:    {c['size_mb']:.2f} MB  →  {c['path']}")
        else:
            print("  Central:    FAILED")
        for slug, info in summary.get("platforms", {}).items():
            print(f"  {slug:12s} {info['size_mb']:.2f} MB  →  {info['path']}")
        if summary.get("resolved"):
            print(f"\n  Resolved {len(summary['resolved'])} spec(s)")
            for r in summary["resolved"]:
                print(f"    → {r}")
        print("═" * 60)


if __name__ == "__main__":
    main_sync()
