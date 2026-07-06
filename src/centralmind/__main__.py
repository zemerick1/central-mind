"""CLI entry point for CentralMind MCP server."""

import argparse
import asyncio
import logging
import secrets
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

from dotenv import load_dotenv

from . import __version__
from .clients_store import ClientsStore
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


def _resolve_spec_paths(config: ServerConfig) -> dict:
    """Resolve any *.json spec that doesn't have a matching *.resolved.json
    yet, and map the results onto platform keys (platform-wide, independent
    of any client's credentials)."""
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

    # Map resolved specs to platform keys (simple heuristic based on filename)
    platform_paths = {}
    if "openAPI" in resolved_spec_paths or "platform" in resolved_spec_paths:
        platform_paths["central"] = resolved_spec_paths.get("openAPI") or resolved_spec_paths.get("platform")
    for platform, stem in [
        ("clearpass", "clearpass-openapi"),
        ("mist", "mist"),
        ("axis", "axis"),
        ("sdc", "sdc"),
        ("uxi", "uxi"),
        ("aoscx", "aoscx"),
    ]:
        if stem in resolved_spec_paths:
            platform_paths[platform] = resolved_spec_paths[stem]

    return platform_paths


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

    clients_store = ClientsStore()
    migrated = clients_store.migrate_from_env(config)
    if migrated:
        logger.info(
            f"Migrated legacy .env credentials into client '{migrated.name}'. "
            f"Manage clients going forward with `centralmind admin`."
        )

    if clients_store.is_empty():
        print(
            "Error: No clients configured. Populate .env and restart, or run "
            "`centralmind admin` to add a client through the web UI.",
            file=sys.stderr,
        )
        sys.exit(1)

    resolved_spec_paths = _resolve_spec_paths(config)

    try:
        server = CentralMindServer(
            config=config,
            clients_store=clients_store,
            resolved_spec_paths=resolved_spec_paths,
        )

        if args.transport == "stdio":
            await server.run_stdio()
        else:  # http
            api_key = args.api_key or clients_store.get_api_key()
            if not api_key:
                api_key = secrets.token_urlsafe(32)
                clients_store.set_api_key(api_key)

            ssl_certfile = ssl_keyfile = None
            if not args.no_tls:
                if bool(args.cert) != bool(args.key):
                    print("Error: --cert and --key must be given together.", file=sys.stderr)
                    sys.exit(1)
                if args.cert and args.key:
                    ssl_certfile, ssl_keyfile = args.cert, args.key
                else:
                    from .tls import ensure_cert
                    cert_path, key_path = ensure_cert()
                    ssl_certfile, ssl_keyfile = str(cert_path), str(key_path)

            await server.run_http(args.host, args.port, api_key, ssl_certfile=ssl_certfile, ssl_keyfile=ssl_keyfile)
    except KeyboardInterrupt:
        logging.info("Server stopped by user")
    except Exception as e:
        logging.error(f"Server error: {e}", exc_info=True)
        sys.exit(1)


async def main_admin(args: argparse.Namespace):
    """Async entry point for the `admin` subcommand."""
    setup_logging(args.debug)
    from .admin_web import run_admin

    clients_store = ClientsStore()
    await run_admin(clients_store, port=args.port)


def _main_tls(argv: list):
    """Synchronous entry point for the `tls` subcommand (cert management)."""
    setup_logging()
    from . import tls

    parser = argparse.ArgumentParser(
        prog="centralmind tls",
        description="Manage the TLS certificate used by `centralmind --transport http`",
    )
    sub = parser.add_subparsers(dest="tls_command", required=True)

    import_parser = sub.add_parser("import", help="Import a cert + key issued by an enterprise or public CA")
    import_parser.add_argument("--cert", required=True, help="Path to the certificate (PEM; a full chain is fine)")
    import_parser.add_argument("--key", required=True, help="Path to the matching unencrypted private key (PEM)")

    sub.add_parser("status", help="Show info about the currently installed certificate")

    generate_parser = sub.add_parser("generate", help="Generate a fresh self-signed certificate")
    generate_parser.add_argument("--force", action="store_true", help="Overwrite an existing certificate")

    args = parser.parse_args(argv)

    if args.tls_command == "import":
        try:
            cert = tls.import_cert(Path(args.cert).read_bytes(), Path(args.key).read_bytes())
        except tls.CertValidationError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Imported certificate for {cert.subject.rfc4514_string()} (valid until {cert.not_valid_after_utc.date()}).")
    elif args.tls_command == "status":
        info = tls.cert_info()
        if info is None:
            print(
                "No certificate installed yet. One is auto-generated (self-signed) the first "
                "time `--transport http` runs, or run `centralmind tls generate` / "
                "`centralmind tls import` now."
            )
        else:
            kind = "self-signed" if info["self_signed"] else "imported (CA-issued)"
            print(f"Subject: {info['subject']}")
            print(f"Issuer:  {info['issuer']}")
            print(f"Type:    {kind}")
            expired_note = " (EXPIRED)" if info["expired"] else ""
            print(f"Valid:   {info['not_valid_before']} .. {info['not_valid_after']}{expired_note}")
    elif args.tls_command == "generate":
        if tls.DEFAULT_CERT_PATH.exists() and not args.force:
            print(
                f"A certificate already exists at {tls.DEFAULT_CERT_PATH}. Use --force to overwrite.",
                file=sys.stderr,
            )
            sys.exit(1)
        tls.generate_self_signed_cert(tls.DEFAULT_CERT_PATH, tls.DEFAULT_KEY_PATH)
        print(f"Generated new self-signed certificate at {tls.DEFAULT_CERT_PATH}")


def main_sync():
    """Synchronous entry point for setup.py console_scripts."""
    # `centralmind admin [...]` / `centralmind tls ...` are handled as distinct,
    # minimal parsers so the default (no subcommand) invocation below keeps its
    # exact existing flag surface for backward compatibility with existing MCP
    # client configs.
    if len(sys.argv) > 1 and sys.argv[1] == "admin":
        admin_parser = argparse.ArgumentParser(
            prog="centralmind admin",
            description="Launch the local (127.0.0.1-only) CentralMind credential admin UI",
        )
        admin_parser.add_argument("--port", type=int, default=8787, help="Admin UI port (default: 8787)")
        admin_parser.add_argument("--debug", action="store_true", help="Enable debug logging")
        admin_args = admin_parser.parse_args(sys.argv[2:])
        asyncio.run(main_admin(admin_args))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "tls":
        _main_tls(sys.argv[2:])
        return

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
        choices=["stdio", "http", "sse"],
        default="stdio",
        help="Transport type (default: stdio)",
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for http transport (default: 127.0.0.1; use 0.0.0.0 for LAN access)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for http transport (default: 8000)",
    )

    parser.add_argument(
        "--api-key",
        default=None,
        help="API key required of http transport clients (Authorization: Bearer <key>). "
        "Auto-generated and persisted if omitted.",
    )

    parser.add_argument(
        "--no-tls",
        action="store_true",
        help="Serve plain HTTP for the http transport instead of HTTPS "
        "(e.g. when a reverse proxy already terminates TLS). Ignored for stdio.",
    )

    parser.add_argument(
        "--cert",
        default=None,
        help="Path to a TLS certificate (PEM) to use for this run, instead of the stored one. "
        "Must be given together with --key. Use `centralmind tls import` to install one permanently.",
    )

    parser.add_argument(
        "--key",
        default=None,
        help="Path to the matching unencrypted private key (PEM) for --cert.",
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

    if args.transport == "sse":
        print(
            "Error: 'sse' transport has been replaced by 'http' (Streamable HTTP, per the "
            "current MCP spec). Only 'stdio' and 'http' are supported.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Run async main
    asyncio.run(main(args))


if __name__ == "__main__":
    main_sync()
