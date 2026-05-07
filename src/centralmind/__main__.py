"""CLI entry point for CentralMind MCP server."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

from dotenv import load_dotenv

from . import __version__
from .auth import CentralAuth
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
    
    # Initialize OAuth2 auth (obtains initial token on startup)
    try:
        auth = CentralAuth(
            client_id=config.central_client_id,
            client_secret=config.central_client_secret,
            base_url=config.central_base_url,
        )
    except RuntimeError as e:
        print(f"Error: Authentication failed: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Determine spec path
    if config.centralmind_spec_path:
        spec_path = Path(config.centralmind_spec_path)
    else:
        # Default: resolved spec in spec/ directory relative to project root
        project_root = Path(__file__).parent.parent.parent
        spec_path = project_root / "spec" / "openAPI.resolved.json"
        source_spec = project_root / "spec" / "openAPI.json"
        
        # Auto-resolve if source is newer than resolved, or resolved doesn't exist
        if source_spec.exists():
            needs_resolve = not spec_path.exists()
            if not needs_resolve:
                needs_resolve = source_spec.stat().st_mtime > spec_path.stat().st_mtime
            
            if needs_resolve:
                logger.info(
                    "Source spec is newer than resolved spec (or resolved spec missing). "
                    "Auto-resolving..."
                )
                try:
                    from .spec_resolver import resolve_spec
                    resolve_spec(str(source_spec), str(spec_path))
                    logger.info("Spec auto-resolved successfully.")
                except Exception as e:
                    logger.error(f"Auto-resolve failed: {e}")
                    if not spec_path.exists():
                        print(
                            f"Error: Could not resolve spec: {e}",
                            file=sys.stderr,
                        )
                        sys.exit(1)
    
    if not spec_path.exists():
        print(
            f"Error: Resolved spec not found at {spec_path}",
            file=sys.stderr,
        )
        print(
            "\nPlease run the spec resolver first:",
            file=sys.stderr,
        )
        print(
            "  python -m centralmind.spec_resolver "
            "spec/openAPI.json spec/openAPI.resolved.json",
            file=sys.stderr,
        )
        sys.exit(1)
    
    # Create and run server
    try:
        server = CentralMindServer(config, auth, str(spec_path))
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
