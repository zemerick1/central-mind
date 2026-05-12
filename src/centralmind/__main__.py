"""CLI entry point for CentralMind MCP server."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

from dotenv import load_dotenv

from . import __version__
from .auth import CentralAuth, ClearpassAuth, MistAuth, SdcAuth
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

    sdc_auth = None
    if config.sdc_apitoken:
        sdc_auth = SdcAuth(
            api_token=config.sdc_apitoken,
            host=config.sdc_host,
        )

    if not central_auth and not clearpass_auth and not mist_auth and not sdc_auth:
        print("Error: No valid authentication credentials provided for Central, ClearPass, Mist, or SDC.", file=sys.stderr)
        sys.exit(1)

    # Determine spec paths
    project_root = Path(__file__).parent.parent.parent
    
    central_spec_path = None
    if central_auth:
        if config.centralmind_spec_path:
            central_spec_path = Path(config.centralmind_spec_path)
        else:
            central_spec_path = project_root / "spec" / "openAPI.resolved.json"
            source_spec = project_root / "spec" / "openAPI.json"
            
            # Auto-resolve if source is newer than resolved, or resolved doesn't exist
            if source_spec.exists():
                needs_resolve = not central_spec_path.exists()
                if not needs_resolve:
                    needs_resolve = source_spec.stat().st_mtime > central_spec_path.stat().st_mtime
                
                if needs_resolve:
                    logger.info("Source spec is newer than resolved spec. Auto-resolving...")
                    try:
                        from .spec_resolver import resolve_spec
                        resolve_spec(str(source_spec), str(central_spec_path))
                        logger.info("Spec auto-resolved successfully.")
                    except Exception as e:
                        logger.error(f"Auto-resolve failed: {e}")
                        if not central_spec_path.exists():
                            print(f"Error: Could not resolve central spec: {e}", file=sys.stderr)
                            sys.exit(1)
        
        if not central_spec_path.exists():
            print(f"Error: Resolved central spec not found at {central_spec_path}", file=sys.stderr)
            sys.exit(1)

    clearpass_spec_path = None
    if clearpass_auth:
        clearpass_spec_path = project_root / "spec" / "clearpass-openapi.json"
        if not clearpass_spec_path.exists():
            print(f"Error: ClearPass spec not found at {clearpass_spec_path}", file=sys.stderr)
            sys.exit(1)

    mist_spec_path = None
    if mist_auth:
        mist_spec_path = project_root / "spec" / "mist.resolved.json"
        if not mist_spec_path.exists():
            print(f"Error: Mist spec not found at {mist_spec_path}", file=sys.stderr)
            sys.exit(1)

    sdc_spec_path = None
    if sdc_auth:
        sdc_spec_path = project_root / "spec" / "sdc.resolved.json"
        if not sdc_spec_path.exists():
            print(f"Error: SDC spec not found at {sdc_spec_path}", file=sys.stderr)
            sys.exit(1)

    # Create and run server
    try:
        server = CentralMindServer(
            config=config,
            central_auth=central_auth,
            central_spec_path=str(central_spec_path) if central_spec_path else None,
            clearpass_auth=clearpass_auth,
            clearpass_spec_path=str(clearpass_spec_path) if clearpass_spec_path else None,
            mist_auth=mist_auth,
            mist_spec_path=str(mist_spec_path) if mist_spec_path else None,
            sdc_auth=sdc_auth,
            sdc_spec_path=str(sdc_spec_path) if sdc_spec_path else None,
        )
        await server.run()
    except KeyboardInterrupt:
        logging.info("Server stopped by user")
    except Exception as e:
        logging.error(f"Server error: {e}", exc_info=True)
        sys.exit(1)


def main_sync():
    """Synchronous entry point for setup.py console_scripts."""
    parser = argparse.ArgumentParser(
        description="CentralMind - Code Mode MCP Server for Aruba Central API",
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version=f"centralmind {__version__}",
    )
    
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport type (default: stdio)",
    )
    
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for SSE transport (default: 127.0.0.1)",
    )
    
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE transport (default: 8000)",
    )
    
    parser.add_argument(
        "--env-file",
        help="Path to .env file to load",
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    
    args = parser.parse_args()
    
    # For now, only stdio is implemented
    if args.transport != "stdio":
        print("Error: Only stdio transport is currently supported", file=sys.stderr)
        sys.exit(1)
    
    # Run async main
    asyncio.run(main(args))


if __name__ == "__main__":
    main_sync()
