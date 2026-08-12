#!/usr/bin/env python3
"""Fetch OpenAPI specs from Aruba's ReadMe-hosted developer hub.

Adapted from nowireless4u/hpe-networking-mcp ``fetch_aruba_oas.py``.

Aruba's developer hub (``developer.arubanetworks.com``) runs on ReadMe's
"SuperHub" platform.  Each project (``new-central``, ``cppm``, ``uxi``, …)
publishes its API reference as **multiple** uploaded OpenAPI definitions.  The
reference page server-renders a ``<script id="ssr-props">`` JSON block
containing:

* ``apiDefinitions`` — the active branch's current specs (filename + uri).
* ``context.project.stable.apiRegistries`` — every uploaded version, each with
  a per-file ``uuid``.

This module parses ssr-props, resolves each filename to its registry ``uuid``,
and fetches the compiled OAS by uuid from the ReadMe dash API::

    https://dash.readme.com/api/v1/api-registry/<uuid>

For **Central** we merge the MRT and Config specs into a single consolidated
``openAPI.json`` (the format this project already consumes).  For other
platforms we write individual ``<platform>.openapi.json`` files.

Usage::

    # Fetch all specs (Central + platforms)
    python -m centralmind.spec_fetcher

    # Central only
    python -m centralmind.spec_fetcher --central-only

    # Also resolve after fetch
    python -m centralmind.spec_fetcher --resolve
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import ssl
import sys
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

HUB = "https://developer.arubanetworks.com"

# ──────────────────────────────────────────────────────────────────────
# Project registry.  "slug" is the ReadMe project path on the hub.
# "central" entries are merged into a single openAPI.json; "platform"
# entries are written as individual <outfile>.openapi.json files.
# ──────────────────────────────────────────────────────────────────────
CENTRAL_PROJECTS: list[dict[str, str]] = [
    {"slug": "new-central", "label": "MRT"},
    {"slug": "new-central-config", "label": "Config"},
]

PLATFORM_PROJECTS: list[dict[str, str]] = [
    {"slug": "uxi", "outfile": "uxi.json"},
    {"slug": "cppm", "outfile": "clearpass.json"},
    {"slug": "aoscx", "outfile": "aoscx.json"},
]

# ──────────────────────────────────────────────────────────────────────
# ReadMe SuperHub scraping
# ──────────────────────────────────────────────────────────────────────
_SSR_PROPS_RE = re.compile(r'<script id="ssr-props"[^>]*>(.*?)</script>', re.DOTALL)
_README_REGISTRY = "https://dash.readme.com/api/v1/api-registry"
_SLUG_RE = re.compile(r"[^a-z0-9]+")

_UA = "Mozilla/5.0 (compatible; centralmind-oas-sync/1.0)"
_TIMEOUT = 30
_RETRIES = 3
_RETRY_BACKOFF = 3  # seconds, multiplied by attempt number

# Module-level SSL verify setting.  Configured by _configure_ssl_verify()
# before any HTTP calls are made.
_ssl_verify: ssl.SSLContext | bool = True


def _configure_ssl_verify(*, no_verify: bool = False) -> None:
    """Configure the module-level SSL verification strategy.

    Priority:
    1. ``--no-verify-ssl`` → disable verification entirely (not recommended).
    2. System cert store via ``ssl.create_default_context()`` → picks up
       corporate proxy CAs that certifi doesn't know about.
    3. Default httpx behaviour (certifi bundle) as final fallback.
    """
    global _ssl_verify  # noqa: PLW0603

    if no_verify:
        logger.warning("SSL verification DISABLED (--no-verify-ssl)")
        _ssl_verify = False
        return

    # Try the system cert store first.  On corporate machines (like HPE
    # laptops behind a TLS-intercepting proxy) the system store carries the
    # proxy's CA cert, which certifi's bundle does not include.
    try:
        ctx = ssl.create_default_context()
        # Quick smoke test — if the system store is empty/broken this will
        # raise before we use it for real traffic.
        if ctx.get_ca_certs():
            logger.debug("Using system SSL cert store (%d CA certs)", len(ctx.get_ca_certs()))
            _ssl_verify = ctx
            return
    except Exception:
        pass

    # Fall back to httpx default (certifi bundle).
    logger.debug("Using certifi CA bundle (system cert store unavailable)")
    _ssl_verify = True


def _http_get(url: str) -> bytes:
    """GET *url* with a browser UA and bounded retries; raise on final failure.

    Uses httpx with the SSL context configured by ``_configure_ssl_verify()``.
    On corporate networks the system cert store is preferred over certifi so
    that TLS-intercepting proxy CAs are trusted automatically.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _RETRIES + 1):
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": _UA},
                timeout=_TIMEOUT,
                follow_redirects=True,
                verify=_ssl_verify,
            )
            resp.raise_for_status()
            return resp.content
        except (httpx.HTTPError, TimeoutError) as exc:
            last_exc = exc
            if attempt < _RETRIES:
                wait = _RETRY_BACKOFF * attempt
                logger.warning("  attempt %d failed (%s), retrying in %ds…", attempt, exc, wait)
                time.sleep(wait)
    raise RuntimeError(f"GET failed after {_RETRIES} attempts: {url} ({last_exc})")


def _slugify(title: str) -> str:
    """Convert an OpenAPI ``info.title`` to a filesystem-stable slug."""
    return _SLUG_RE.sub("-", title.strip().lower()).strip("-") or "untitled"


def _looks_like_oas(obj: Any) -> bool:
    """True when *obj* is an OpenAPI/Swagger doc carrying at least one path."""
    return (
        isinstance(obj, dict)
        and bool(obj.get("openapi") or obj.get("swagger"))
        and isinstance(obj.get("paths"), dict)
        and len(obj["paths"]) > 0
    )


# ──────────────────────────────────────────────────────────────────────
# Discovery + fetch
# ──────────────────────────────────────────────────────────────────────
def _parse_ssr_props(slug: str) -> dict[str, Any]:
    """Parse the ``<script id="ssr-props">`` JSON from a project's /reference page."""
    logger.info("  fetching %s/%s/reference …", HUB, slug)
    page = _http_get(f"{HUB}/{slug}/reference").decode("utf-8", "replace")
    match = _SSR_PROPS_RE.search(page)
    if not match:
        raise RuntimeError(f"no ssr-props on {HUB}/{slug}/reference (portal structure changed?)")
    try:
        return json.loads(_html.unescape(match.group(1).strip()))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{slug}: ssr-props JSON did not parse ({exc})") from exc


def discover_specs(slug: str) -> list[dict[str, str]]:
    """Discover the CURRENT branch's OpenAPI specs as ``[{filename, uuid}]``.

    ``apiDefinitions`` is the active branch's live set; ``apiRegistries`` holds
    every uploaded version keyed by a per-file ``uuid`` — which is what the raw
    OAS endpoint fetches by.  We take the current filenames and resolve each to
    its registry uuid (last/most-recent entry wins on a duplicate filename).
    """
    props = _parse_ssr_props(slug)
    api_defs = props.get("apiDefinitions") or []
    registries = (
        (((props.get("context") or {}).get("project") or {}).get("stable") or {})
        .get("apiRegistries") or []
    )

    uuid_by_file: dict[str, str] = {}
    for reg in registries:
        filename, uuid = reg.get("filename"), reg.get("uuid")
        if filename and uuid:
            uuid_by_file[filename] = uuid  # later entries (more recent) override

    specs: list[dict[str, str]] = []
    for definition in api_defs:
        if definition.get("type") not in (None, "openapi"):
            continue  # skip non-OpenAPI definitions
        filename = definition.get("filename")
        uuid = uuid_by_file.get(filename or "")
        if not uuid:
            continue
        specs.append({"filename": filename, "uuid": uuid})

    if not specs:
        raise RuntimeError(f"{slug}: no OpenAPI definitions found in ssr-props apiDefinitions")
    return specs


def fetch_spec(uuid: str) -> dict[str, Any] | None:
    """Fetch one compiled OAS by its ReadMe api-registry uuid.

    Returns the OAS dict, or None if the response isn't a valid OpenAPI doc.
    """
    raw = _http_get(f"{_README_REGISTRY}/{uuid}")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return obj if _looks_like_oas(obj) else None


def fetch_all_specs_for_project(slug: str) -> list[dict[str, Any]]:
    """Discover and fetch all valid OpenAPI specs for a project.

    Returns a list of parsed OpenAPI spec dicts.
    """
    discovered = discover_specs(slug)
    logger.info("  discovered %d definition(s) for '%s'", len(discovered), slug)

    specs: list[dict[str, Any]] = []
    for entry in discovered:
        uuid = entry["uuid"]
        logger.info("  fetching %s (uuid=%s) …", entry["filename"], uuid[:12])
        spec = fetch_spec(uuid)
        if spec is not None:
            path_count = len(spec.get("paths", {}))
            title = spec.get("info", {}).get("title", "?")
            logger.info("    ✓ %s — %d paths", title, path_count)
            specs.append(spec)
        else:
            logger.warning("    ✗ uuid %s did not return a valid OAS", uuid)

    return specs


# ──────────────────────────────────────────────────────────────────────
# Merging Central MRT + Config into a single openAPI.json
# ──────────────────────────────────────────────────────────────────────
def merge_specs(specs: list[dict[str, Any]], *, title: str | None = None) -> dict[str, Any]:
    """Merge multiple OpenAPI 3.x specs into one consolidated document.

    Paths, tags, components/schemas, components/responses, and
    components/parameters are merged.  Collisions on path keys raise;
    collisions on component names silently keep the first definition
    (the specs are from the same vendor so duplicates are identical).
    """
    merged: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": title or "HPE Aruba Networking Central API",
            "description": (
                "Consolidated OpenAPI 3.1 specification for HPE Aruba Networking Central, "
                "covering both MRT (Monitoring, Reporting & Troubleshooting) APIs and "
                "Configuration APIs.  Auto-generated from official ReadMe developer hub."
            ),
            "version": "1.0.0",
            "contact": {
                "name": "HPE Aruba Networking",
                "url": "https://developer.arubanetworks.com",
                "email": "aruba-automation@hpe.com",
            },
        },
        "servers": [
            {
                "url": "{baseUrl}",
                "description": (
                    "HPE Aruba Networking Central API Gateway. "
                    "The base URL varies by geographical cluster."
                ),
                "variables": {
                    "baseUrl": {
                        "default": "https://internal-apigw.central.arubanetworks.com",
                        "description": (
                            "Domain Base URL for HPE Aruba Networking Central API Gateway "
                            "based on the geographical cluster where your account is registered."
                        ),
                    }
                },
            }
        ],
        "security": [{"BearerAuth": []}],
        "tags": [],
        "paths": {},
        "components": {
            "schemas": {},
            "responses": {},
            "parameters": {},
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                },
            },
        },
    }

    seen_tags: set[str] = set()

    for spec in specs:
        # Merge tags (deduplicate by name)
        for tag in spec.get("tags", []):
            name = tag.get("name", "")
            if name and name not in seen_tags:
                seen_tags.add(name)
                merged["tags"].append(tag)

        # Merge paths
        for path, path_item in spec.get("paths", {}).items():
            if path in merged["paths"]:
                # Same path from multiple specs — merge methods
                for method, op in path_item.items():
                    if method not in merged["paths"][path]:
                        merged["paths"][path][method] = op
                    # else: duplicate method on same path, keep first
            else:
                merged["paths"][path] = path_item

        # Merge components (schemas, responses, parameters)
        for comp_type in ("schemas", "responses", "parameters"):
            source = spec.get("components", {}).get(comp_type, {})
            target = merged["components"][comp_type]
            for name, definition in source.items():
                if name not in target:
                    target[name] = definition

        # Carry over securitySchemes if present
        for name, scheme in spec.get("components", {}).get("securitySchemes", {}).items():
            if name not in merged["components"]["securitySchemes"]:
                merged["components"]["securitySchemes"][name] = scheme

    # Sort tags by name for deterministic output
    merged["tags"].sort(key=lambda t: t.get("name", ""))

    return merged


# ──────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────
def fetch_central_spec(spec_dir: Path) -> Path:
    """Fetch Central MRT + Config specs, merge, and write to spec_dir/central.json.

    Returns the path to the written file.
    """
    output = spec_dir / "central.json"

    logger.info("Fetching Central API specs from %s …", HUB)
    all_specs: list[dict[str, Any]] = []
    for project in CENTRAL_PROJECTS:
        slug = project["slug"]
        label = project["label"]
        logger.info("── %s (%s) ──", label, slug)
        specs = fetch_all_specs_for_project(slug)
        all_specs.extend(specs)

    if not all_specs:
        raise RuntimeError("No valid Central specs fetched — cannot generate central.json")

    total_paths = sum(len(s.get("paths", {})) for s in all_specs)
    logger.info("Merging %d definition(s) (%d total paths) …", len(all_specs), total_paths)

    merged = merge_specs(all_specs)
    merged_paths = len(merged.get("paths", {}))
    merged_schemas = len(merged.get("components", {}).get("schemas", {}))

    spec_dir.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    size_mb = output.stat().st_size / (1024 * 1024)
    logger.info(
        "✓ Wrote %s — %d paths, %d schemas (%.2f MB)",
        output.name, merged_paths, merged_schemas, size_mb,
    )
    return output


def fetch_platform_spec(slug: str, outfile: str, spec_dir: Path) -> Path | None:
    """Fetch all specs for a platform project and write as a single merged file.

    Returns the path to the written file, or None on failure.
    """
    output = spec_dir / outfile
    logger.info("── %s ──", slug)

    try:
        specs = fetch_all_specs_for_project(slug)
    except Exception as exc:
        logger.error("  ✗ %s: %s", slug, exc)
        return None

    if not specs:
        logger.warning("  ✗ %s: no valid specs", slug)
        return None

    # If there's only one spec, write it directly; otherwise merge
    if len(specs) == 1:
        result = specs[0]
    else:
        result = merge_specs(specs, title=specs[0].get("info", {}).get("title"))

    spec_dir.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    path_count = len(result.get("paths", {}))
    size_mb = output.stat().st_size / (1024 * 1024)
    logger.info("  ✓ Wrote %s — %d paths (%.2f MB)", outfile, path_count, size_mb)
    return output


def fetch_all_specs(
    spec_dir: Path,
    *,
    central_only: bool = False,
    resolve: bool = False,
) -> dict[str, Any]:
    """Fetch all configured specs and optionally resolve them.

    Returns a summary dict with counts and file paths.
    """
    summary: dict[str, Any] = {"central": None, "platforms": {}, "resolved": []}

    # ── Central ──
    try:
        central_path = fetch_central_spec(spec_dir)
        summary["central"] = {
            "path": str(central_path),
            "size_mb": round(central_path.stat().st_size / (1024 * 1024), 2),
        }
    except Exception as exc:
        logger.error("Central fetch failed: %s", exc)

    # ── Platform specs ──
    if not central_only:
        logger.info("\nFetching platform specs …")
        for project in PLATFORM_PROJECTS:
            path = fetch_platform_spec(project["slug"], project["outfile"], spec_dir)
            if path:
                summary["platforms"][project["slug"]] = {
                    "path": str(path),
                    "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
                }

    # ── Optional: resolve ──
    if resolve:
        logger.info("\nResolving specs …")
        from .spec_resolver import resolve_spec

        # Resolve Central
        central_source = spec_dir / "central.json"
        central_resolved = spec_dir / "central.resolved.json"
        if central_source.exists():
            logger.info("Resolving %s → %s …", central_source.name, central_resolved.name)
            try:
                resolve_spec(str(central_source), str(central_resolved))
                summary["resolved"].append(str(central_resolved))
            except Exception as exc:
                logger.error("Failed to resolve %s: %s", central_source.name, exc)

        # Resolve platform specs that have a matching .resolved.json convention
        resolve_pairs = [
            ("clearpass.json", "clearpass.resolved.json"),
            ("aoscx.json", "aoscx.resolved.json"),
            ("uxi.json", "uxi.resolved.json"),
            ("mist.json", "mist.resolved.json"),
            ("axis.json", "axis.resolved.json"),
            ("sdc.json", "sdc.resolved.json"),
        ]
        for source_name, resolved_name in resolve_pairs:
            source = spec_dir / source_name
            resolved = spec_dir / resolved_name
            if source.exists():
                logger.info("Resolving %s → %s …", source_name, resolved_name)
                try:
                    resolve_spec(str(source), str(resolved))
                    summary["resolved"].append(str(resolved))
                except Exception as exc:
                    logger.error("Failed to resolve %s: %s", source_name, exc)

    return summary


# ──────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    """CLI entry point for spec fetching."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch OpenAPI specs from Aruba's developer hub and optionally resolve them.",
    )
    parser.add_argument(
        "--central-only",
        action="store_true",
        help="Only fetch Central (MRT + Config) specs, skip platform specs.",
    )
    parser.add_argument(
        "--resolve",
        action="store_true",
        help="Also run the $ref resolver after fetching to generate .resolved.json files.",
    )
    parser.add_argument(
        "--spec-dir",
        type=Path,
        default=None,
        help="Output directory for spec files (default: <project_root>/spec/).",
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Disable SSL certificate verification (use when corporate proxy certs cause issues).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Configure SSL before any HTTP calls
    _configure_ssl_verify(no_verify=args.no_verify_ssl)

    spec_dir = args.spec_dir
    if spec_dir is None:
        spec_dir = Path(__file__).resolve().parent.parent.parent / "spec"

    logger.info("Spec output directory: %s", spec_dir)

    try:
        summary = fetch_all_specs(
            spec_dir,
            central_only=args.central_only,
            resolve=args.resolve,
        )
    except Exception as exc:
        logger.error("Fatal: %s", exc, exc_info=True)
        return 1

    # Print summary
    print("\n" + "═" * 60)
    print("  Spec Fetch Summary")
    print("═" * 60)

    if summary["central"]:
        c = summary["central"]
        print(f"  Central:    {c['size_mb']:.2f} MB  →  {c['path']}")
    else:
        print("  Central:    FAILED")

    for slug, info in summary["platforms"].items():
        print(f"  {slug:12s} {info['size_mb']:.2f} MB  →  {info['path']}")

    if summary["resolved"]:
        print(f"\n  Resolved {len(summary['resolved'])} spec(s)")
        for r in summary["resolved"]:
            print(f"    → {r}")

    print("═" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
