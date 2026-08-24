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

    # Map resolved specs to platform keys. Specs were renamed from
    # *.openapi.json to *.json (e.g. spec/central.json, spec/clearpass.json)
    # — the clean stems are checked first, with the old names kept as a
    # fallback for anyone still on pre-rename spec files.
    platform_paths = {}
    central_stem = resolved_spec_paths.get("central") or resolved_spec_paths.get("openAPI") or resolved_spec_paths.get("platform")
    if central_stem:
        platform_paths["central"] = central_stem
    clearpass_stem = resolved_spec_paths.get("clearpass") or resolved_spec_paths.get("clearpass-openapi")
    if clearpass_stem:
        platform_paths["clearpass"] = clearpass_stem
    for platform in ("mist", "axis", "sdc", "uxi", "aoscx"):
        if platform in resolved_spec_paths:
            platform_paths[platform] = resolved_spec_paths[platform]

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

    # An admin-configured server API mode (set via `centralmind admin` ->
    # Server Settings) overrides CENTRALMIND_API_MODE/.env for this launch.
    # This is read once, here, at startup — changing it in the admin UI
    # while this process is already running has no effect until it's
    # restarted.
    stored_api_mode = clients_store.get_server_api_mode()
    if stored_api_mode:
        logger.info(
            f"Using server API mode '{stored_api_mode}' from the admin UI's Server Settings "
            f"(overrides CENTRALMIND_API_MODE={config.centralmind_api_mode!r})."
        )
        config.centralmind_api_mode = stored_api_mode

    if clients_store.is_empty():
        print(
            "Error: No clients configured. Populate .env and restart, or run "
            "`centralmind admin` to add a client through the web UI.",
            file=sys.stderr,
        )
        sys.exit(1)

    resolved_spec_paths = _resolve_spec_paths(config)

    # CENTRALMIND_SPEC_PATH overrides the auto-resolved Central spec when set.
    if config.centralmind_spec_path:
        override = Path(config.centralmind_spec_path)
        if not override.exists():
            print(f"Error: Resolved central spec not found at {override}", file=sys.stderr)
            sys.exit(1)
        resolved_spec_paths["central"] = str(override)

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


def _add_serve_args(serve_parser: argparse.ArgumentParser) -> None:
    """Shared flag definitions for both the legacy flat invocation and the
    explicit `serve` subcommand — kept in one place so the two stay in sync."""
    serve_parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default="stdio",
        help="Transport type (default: stdio)",
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for http transport (default: 127.0.0.1; use 0.0.0.0 for LAN access)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for http transport (default: 8000)",
    )
    serve_parser.add_argument(
        "--api-key",
        default=None,
        help="API key required of http transport clients (Authorization: Bearer <key>). "
        "Auto-generated and persisted if omitted.",
    )
    serve_parser.add_argument(
        "--no-tls",
        action="store_true",
        help="Serve plain HTTP for the http transport instead of HTTPS "
        "(e.g. when a reverse proxy already terminates TLS). Ignored for stdio.",
    )
    serve_parser.add_argument(
        "--cert",
        default=None,
        help="Path to a TLS certificate (PEM) to use for this run, instead of the stored one. "
        "Must be given together with --key. Use `centralmind tls import` to install one permanently.",
    )
    serve_parser.add_argument(
        "--key",
        default=None,
        help="Path to the matching unencrypted private key (PEM) for --cert.",
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


def _reject_sse(args: argparse.Namespace) -> None:
    if args.transport == "sse":
        print(
            "Error: 'sse' transport has been replaced by 'http' (Streamable HTTP, per the "
            "current MCP spec). Only 'stdio' and 'http' are supported.",
            file=sys.stderr,
        )
        sys.exit(1)


def _run_fetch_specs(args: argparse.Namespace) -> None:
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


def main_sync():
    """Synchronous entry point for setup.py console_scripts."""
    # `centralmind admin [...]` / `centralmind tls ...` are handled as distinct,
    # minimal parsers up front so they keep working regardless of the
    # serve/fetch-specs subcommand machinery below.
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

    # ── Step 1: lightweight pre-parse to detect whether a subcommand was
    #    given. This avoids the bug where `--env-file .env` (no subcommand)
    #    is swallowed by the subparser as a positional arg, producing
    #    "invalid choice: '/path/to/.env'".
    _SUBCOMMANDS = {"serve", "fetch-specs"}
    has_subcommand = any(tok in _SUBCOMMANDS for tok in sys.argv[1:])

    if not has_subcommand:
        # Legacy / backwards-compatible flat-arg invocation:
        #   python -m centralmind --env-file .env --debug
        # No subcommand → treat as "serve". Existing MCP client configs
        # (Claude Desktop, etc.) rely on this shape, so it carries the full
        # serve flag set, not just the subset upstream originally had.
        parser_compat = argparse.ArgumentParser(
            description="CentralMind - Code Mode MCP Server for Aruba Central API",
        )
        parser_compat.add_argument(
            "--version", action="version", version=f"centralmind {__version__}",
        )
        _add_serve_args(parser_compat)
        args = parser_compat.parse_args()

        _reject_sse(args)
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
    _add_serve_args(serve_parser)

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
        _reject_sse(args)
        asyncio.run(main(args))
    elif args.command == "fetch-specs":
        _run_fetch_specs(args)


if __name__ == "__main__":
    main_sync()
