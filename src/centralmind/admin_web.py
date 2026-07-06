"""Loopback-only local web UI for managing multi-client credentials.

Always binds to 127.0.0.1 regardless of what host the main MCP server is
configured with — credential entry never goes out over the network. Gated
by a token generated fresh on every launch (printed to the console, Jupyter
-style) since an unauthenticated localhost admin page holding every
customer's API secrets is itself a soft target (e.g. a malicious page open
in the same browser could otherwise POST to it).
"""

import html
import logging
import secrets
from typing import Optional
from urllib.parse import quote, urlparse

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from . import tls
from .clients_store import API_MODE_ORDER, PLATFORM_FIELD_GROUPS, ClientProfile, ClientsStore
from .platform_factory import build_platform_auth

logger = logging.getLogger(__name__)

ADMIN_TOKEN_COOKIE = "cm_admin_token"

PLATFORM_LABELS = {
    "central": "HPE Aruba Networking Central",
    "clearpass": "HPE Aruba ClearPass",
    "mist": "HPE Juniper Mist",
    "axis": "HPE Axis Security",
    "sdc": "HPE Networking Security Director Cloud",
    "uxi": "HPE Aruba Networking UXI",
    "aoscx": "HPE Aruba Networking AOS-CX",
}

API_MODE_CHOICES = [
    ("", "Inherit server default"),
    ("readonly", "Read-only"),
    ("readwrite", "Read/write"),
    ("all", "All (includes delete)"),
]

_STYLE = """
body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
h1, h2 { font-weight: 600; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid #ddd; }
fieldset { margin-bottom: 1rem; border: 1px solid #ddd; border-radius: 6px; }
legend { font-weight: 600; padding: 0 0.5rem; }
label { display: block; margin: 0.5rem 0 0.2rem; font-size: 0.9rem; color: #444; }
input[type=text], input[type=password], input[type=url], select { width: 100%; padding: 0.4rem; box-sizing: border-box; }
button, .btn { padding: 0.4rem 0.9rem; border-radius: 4px; border: 1px solid #888; background: #f5f5f5; cursor: pointer; }
.btn-danger { border-color: #c33; color: #c33; }
.pill { display: inline-block; background: #eef; border-radius: 10px; padding: 0.1rem 0.6rem; font-size: 0.8rem; margin-right: 0.3rem; }
.banner-ok { background: #e6ffed; border: 1px solid #3c3; padding: 0.6rem; border-radius: 6px; margin-bottom: 1rem; }
.banner-err { background: #ffecec; border: 1px solid #c33; padding: 0.6rem; border-radius: 6px; margin-bottom: 1rem; }
.field-err { background: #ffecec; border: 1px solid #c33; padding: 0.3rem 0.6rem; margin: 0.2rem 0; border-radius: 4px; font-size: 0.85rem; }
.hint { font-size: 0.85rem; color: #666; margin-top: 0.4rem; }
.actions form { display: inline; }
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><title>{html.escape(title)}</title>"
        f"<style>{_STYLE}</style></head><body>{body}</body></html>"
    )


def _error_html(message: Optional[str]) -> str:
    if not message:
        return ""
    return f'<div class="field-err">{html.escape(message)}</div>'


def _is_bool_field(field_name: str) -> bool:
    return ClientProfile.model_fields[field_name].annotation is bool


def _validate_platform_fields(kwargs: dict) -> dict:
    """Check *_base_url fields are full http(s) URLs and *_host fields are
    bare hostnames (not URLs). Returns {field_name: error_message}."""
    errors = {}
    for fields in PLATFORM_FIELD_GROUPS.values():
        for field_name, label, _is_secret in fields:
            value = kwargs.get(field_name)
            if not isinstance(value, str) or not value:
                continue  # empty is fine — that just means "not configured"
            if field_name.endswith("_base_url"):
                parsed = urlparse(value)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    errors[field_name] = f"{label} must be a full URL starting with http:// or https://"
            elif field_name.endswith("_host"):
                if "://" in value or "/" in value or value != value.strip():
                    errors[field_name] = f"{label} must be a bare hostname (e.g. api.example.com), not a full URL"
    return errors


def _api_mode_field(current: Optional[str], error: Optional[str] = None) -> str:
    current = current or ""
    options_html = "".join(
        f'<option value="{value}"{" selected" if value == current else ""}>{html.escape(label)}</option>'
        for value, label in API_MODE_CHOICES
    )
    return (
        "<fieldset><legend>API Access Mode</legend>"
        '<label for="api_mode">Mode for this client</label>'
        f'<select id="api_mode" name="api_mode">{options_html}</select>'
        f"{_error_html(error)}"
        '<p class="hint">The server\'s own launch-time mode (<code>CENTRALMIND_API_MODE</code>) is always a '
        "hard ceiling — this can restrict a client further, but can never grant more access than the server "
        "itself allows. Check <code>list_clients</code> from an MCP session to see the effective mode.</p>"
        "</fieldset>"
    )


def _profile_form_fields(values: dict, errors: dict, existing_profile: Optional[ClientProfile]) -> str:
    sections = []
    for platform, fields in PLATFORM_FIELD_GROUPS.items():
        rows = []
        for field_name, label, is_secret in fields:
            if _is_bool_field(field_name):
                checked = "checked" if values.get(field_name) else ""
                rows.append(
                    f'<label><input type="checkbox" name="{field_name}" {checked}> {html.escape(label)}</label>'
                )
            else:
                input_type = "password" if is_secret else "text"
                if is_secret:
                    has_saved = bool(existing_profile and getattr(existing_profile, field_name, ""))
                    display_value = ""
                    placeholder = "(unchanged — leave blank to keep current value)" if has_saved else ""
                else:
                    display_value = html.escape(str(values.get(field_name, "") or ""))
                    placeholder = ""
                rows.append(
                    f'<label for="{field_name}">{html.escape(label)}</label>'
                    f'<input type="{input_type}" id="{field_name}" name="{field_name}" '
                    f'value="{display_value}" placeholder="{placeholder}">'
                    f"{_error_html(errors.get(field_name))}"
                )
        test_button = ""
        if existing_profile and platform in existing_profile.configured_platforms():
            test_button = (
                f'<button formaction="/clients/{existing_profile.id}/test/{platform}" '
                f'formmethod="post" formnovalidate>Test Connection</button>'
            )
        sections.append(
            f"<fieldset><legend>{html.escape(PLATFORM_LABELS[platform])}</legend>"
            f"{''.join(rows)}{test_button}</fieldset>"
        )
    return "".join(sections)


def _render_client_form(values: dict, errors: dict, existing_profile: Optional[ClientProfile], action_url: str) -> str:
    name_value = html.escape(str(values.get("name", "")))
    return (
        f'<form method="post" action="{action_url}">'
        '<label for="name">Client name</label>'
        f'<input type="text" id="name" name="name" value="{name_value}" required>'
        f"{_error_html(errors.get('name'))}"
        f"{_api_mode_field(values.get('api_mode'), errors.get('api_mode'))}"
        f"{_profile_form_fields(values, errors, existing_profile)}"
        '<button type="submit">Save</button></form>'
    )


def _extract_profile_kwargs(form) -> dict:
    kwargs = {}
    for fields in PLATFORM_FIELD_GROUPS.values():
        for field_name, _label, is_secret in fields:
            if _is_bool_field(field_name):
                kwargs[field_name] = field_name in form
            elif field_name in form:
                value = form[field_name]
                if is_secret and value == "":
                    continue  # blank secret field = "leave unchanged"
                kwargs[field_name] = value
    return kwargs


def _extract_api_mode(form) -> tuple:
    """Returns (api_mode_or_None, error_or_None)."""
    raw = form.get("api_mode", "").strip()
    if not raw:
        return None, None
    if raw not in API_MODE_ORDER:
        return None, "Invalid API mode."
    return raw, None


def create_admin_app(clients_store: ClientsStore, admin_token: str) -> Starlette:
    """Build the Starlette admin application. Caller is responsible for
    binding it to 127.0.0.1 only."""

    def _authorized(request: Request) -> bool:
        return request.cookies.get(ADMIN_TOKEN_COOKIE) == admin_token or (
            request.query_params.get("token") == admin_token
        )

    async def gate_or(request: Request, handler):
        if not _authorized(request):
            return _page(
                "CentralMind Admin — Unauthorized",
                "<h1>Unauthorized</h1><p>Missing or invalid admin token. "
                "Use the URL printed in the server console when you ran "
                "<code>centralmind admin</code>.</p>",
            )
        response = await handler(request)
        if request.query_params.get("token") == admin_token and not request.cookies.get(ADMIN_TOKEN_COOKIE):
            response.set_cookie(ADMIN_TOKEN_COOKIE, admin_token, httponly=True, samesite="lax")
        return response

    async def index(request: Request):
        async def handler(request: Request):
            rows = []
            default_id = clients_store.get_default_id()
            for p in clients_store.list():
                platforms = "".join(f'<span class="pill">{html.escape(pl)}</span>' for pl in p.configured_platforms()) or "<em>none</em>"
                default_marker = " ⭐ default" if p.id == default_id else ""
                mode_label = p.api_mode or "inherit"
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(p.name)}{default_marker}</td>"
                    f"<td>{platforms}</td>"
                    f"<td>{html.escape(mode_label)}</td>"
                    f'<td class="actions">'
                    f'<a class="btn" href="/clients/{p.id}/edit?token={html.escape(admin_token)}">Edit</a> '
                    f'<form method="post" action="/clients/{p.id}/set-default"><button>Set default</button></form> '
                    f'<form method="post" action="/clients/{p.id}/delete" onsubmit="return confirm(\'Delete {html.escape(p.name)}?\');">'
                    f'<button class="btn-danger">Delete</button></form>'
                    "</td></tr>"
                )
            table = (
                "<table><tr><th>Client</th><th>Configured platforms</th><th>API mode</th><th>Actions</th></tr>"
                + "".join(rows)
                + "</table>"
                if rows
                else "<p>No clients configured yet.</p>"
            )
            body = (
                "<h1>CentralMind — Multi-Client Setup</h1>"
                f'<p><a class="btn" href="/clients/new?token={html.escape(admin_token)}">+ Add client</a> '
                f'<a class="btn" href="/tls?token={html.escape(admin_token)}">TLS certificate</a></p>'
                f"{table}"
            )
            return _page("CentralMind Admin", body)

        return await gate_or(request, handler)

    async def new_client_form(request: Request):
        async def handler(request: Request):
            body = "<h1>Add Client</h1>" + _render_client_form({}, {}, None, "/clients/new")
            return _page("Add Client", body)

        return await gate_or(request, handler)

    async def create_client(request: Request):
        async def handler(request: Request):
            form = await request.form()
            name = form.get("name", "").strip()
            kwargs = _extract_profile_kwargs(form)
            api_mode, api_mode_error = _extract_api_mode(form)

            errors = _validate_platform_fields(kwargs)
            if not name:
                errors["name"] = "Client name is required."
            if api_mode_error:
                errors["api_mode"] = api_mode_error

            if errors:
                values = dict(kwargs)
                values["name"] = name
                values["api_mode"] = api_mode or ""
                body = "<h1>Add Client</h1>" + _render_client_form(values, errors, None, "/clients/new")
                return _page("Add Client", body)

            clients_store.create(name=name, api_mode=api_mode, **kwargs)
            return RedirectResponse(f"/?token={admin_token}", status_code=303)

        return await gate_or(request, handler)

    async def edit_client_form(request: Request):
        async def handler(request: Request):
            client_id = request.path_params["client_id"]
            profile = clients_store.get(client_id)
            if profile is None:
                return _page("Not found", "<p>No such client.</p>")
            banner = ""
            test_result = request.query_params.get("test_result")
            if test_result:
                platform, _, outcome = test_result.partition(":")
                cls = "banner-ok" if outcome == "ok" else "banner-err"
                msg = "Connection succeeded." if outcome == "ok" else html.escape(outcome)
                banner = f'<div class="{cls}">{html.escape(platform)}: {msg}</div>'
            values = profile.model_dump()
            body = (
                f"<h1>Edit Client: {html.escape(profile.name)}</h1>"
                f"{banner}"
                f"{_render_client_form(values, {}, profile, f'/clients/{profile.id}/edit')}"
            )
            return _page(f"Edit {profile.name}", body)

        return await gate_or(request, handler)

    async def update_client(request: Request):
        async def handler(request: Request):
            client_id = request.path_params["client_id"]
            profile = clients_store.get(client_id)
            if profile is None:
                return _page("Not found", "<p>No such client.</p>")
            form = await request.form()
            name = form.get("name", "").strip()
            kwargs = _extract_profile_kwargs(form)
            api_mode, api_mode_error = _extract_api_mode(form)

            errors = _validate_platform_fields(kwargs)
            if not name:
                errors["name"] = "Client name is required."
            if api_mode_error:
                errors["api_mode"] = api_mode_error

            if errors:
                values = profile.model_dump()
                values.update(kwargs)
                values["name"] = name
                values["api_mode"] = api_mode or ""
                body = (
                    f"<h1>Edit Client: {html.escape(profile.name)}</h1>"
                    f"{_render_client_form(values, errors, profile, f'/clients/{profile.id}/edit')}"
                )
                return _page(f"Edit {profile.name}", body)

            if name:
                profile.name = name
            for field_name, value in kwargs.items():
                setattr(profile, field_name, value)
            profile.api_mode = api_mode
            clients_store.save(profile)
            return RedirectResponse(f"/?token={admin_token}", status_code=303)

        return await gate_or(request, handler)

    async def delete_client(request: Request):
        async def handler(request: Request):
            client_id = request.path_params["client_id"]
            clients_store.delete(client_id)
            return RedirectResponse(f"/?token={admin_token}", status_code=303)

        return await gate_or(request, handler)

    async def set_default_client(request: Request):
        async def handler(request: Request):
            client_id = request.path_params["client_id"]
            clients_store.set_default(client_id)
            return RedirectResponse(f"/?token={admin_token}", status_code=303)

        return await gate_or(request, handler)

    async def test_connection(request: Request):
        async def handler(request: Request):
            client_id = request.path_params["client_id"]
            platform = request.path_params["platform"]
            profile = clients_store.get(client_id)
            if profile is None:
                return _page("Not found", "<p>No such client.</p>")
            try:
                build_platform_auth(platform, profile)
                outcome = "ok"
            except Exception as e:
                outcome = str(e).replace("\n", " ")
            test_result = quote(f"{platform}:{outcome}", safe="")
            return RedirectResponse(
                f"/clients/{client_id}/edit?token={admin_token}&test_result={test_result}",
                status_code=303,
            )

        return await gate_or(request, handler)

    async def tls_page(request: Request):
        async def handler(request: Request):
            info = tls.cert_info()
            banner = ""
            tls_result = request.query_params.get("tls_result")
            if tls_result:
                cls = "banner-ok" if tls_result.startswith("ok:") else "banner-err"
                banner = f'<div class="{cls}">{html.escape(tls_result.split(":", 1)[-1])}</div>'

            if info is None:
                status_html = "<p>No certificate installed yet — one is generated automatically the first time <code>--transport http</code> runs.</p>"
            else:
                kind = "Self-signed" if info["self_signed"] else "Imported (CA-issued)"
                expired = " — <strong>EXPIRED</strong>" if info["expired"] else ""
                status_html = (
                    f"<table>"
                    f"<tr><th>Type</th><td>{html.escape(kind)}</td></tr>"
                    f"<tr><th>Subject</th><td>{html.escape(info['subject'])}</td></tr>"
                    f"<tr><th>Issuer</th><td>{html.escape(info['issuer'])}</td></tr>"
                    f"<tr><th>Valid</th><td>{html.escape(info['not_valid_before'])} .. {html.escape(info['not_valid_after'])}{expired}</td></tr>"
                    f"</table>"
                )

            body = (
                "<h1>TLS Certificate</h1>"
                f"{banner}"
                f"{status_html}"
                "<fieldset><legend>Generate self-signed certificate</legend>"
                "<p>Covers localhost, this machine's hostname, and its detected local IP addresses. "
                "Clients will need to accept a trust warning unless you install this cert in their trust store.</p>"
                f'<form method="post" action="/tls/generate"><button>Generate new self-signed certificate</button></form>'
                "</fieldset>"
                "<fieldset><legend>Import certificate from an enterprise or public CA</legend>"
                "<p>Upload a certificate (PEM, a full chain is fine) and its matching unencrypted private key. "
                "This replaces whatever certificate is currently installed.</p>"
                '<form method="post" action="/tls/import" enctype="multipart/form-data">'
                '<label for="cert_file">Certificate (.pem/.crt)</label>'
                '<input type="file" id="cert_file" name="cert_file" required>'
                '<label for="key_file">Private key (.pem/.key)</label>'
                '<input type="file" id="key_file" name="key_file" required>'
                '<button type="submit">Import</button></form>'
                "</fieldset>"
            )
            return _page("TLS Certificate", body)

        return await gate_or(request, handler)

    async def tls_generate(request: Request):
        async def handler(request: Request):
            tls.generate_self_signed_cert(tls.DEFAULT_CERT_PATH, tls.DEFAULT_KEY_PATH)
            outcome = quote("ok:Generated a new self-signed certificate.", safe="")
            return RedirectResponse(f"/tls?token={admin_token}&tls_result={outcome}", status_code=303)

        return await gate_or(request, handler)

    async def tls_import(request: Request):
        async def handler(request: Request):
            form = await request.form()
            cert_file = form.get("cert_file")
            key_file = form.get("key_file")
            if cert_file is None or key_file is None:
                return RedirectResponse(
                    f"/tls?token={admin_token}&tls_result=err:Both a certificate and key file are required.",
                    status_code=303,
                )
            cert_bytes = await cert_file.read()
            key_bytes = await key_file.read()
            try:
                cert = tls.import_cert(cert_bytes, key_bytes)
                outcome = f"ok:Imported certificate for {cert.subject.rfc4514_string()}."
            except tls.CertValidationError as e:
                outcome = f"err:{e}"
            return RedirectResponse(f"/tls?token={admin_token}&tls_result={quote(outcome, safe='')}", status_code=303)

        return await gate_or(request, handler)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/clients/new", new_client_form, methods=["GET"]),
            Route("/clients/new", create_client, methods=["POST"]),
            Route("/clients/{client_id}/edit", edit_client_form, methods=["GET"]),
            Route("/clients/{client_id}/edit", update_client, methods=["POST"]),
            Route("/clients/{client_id}/delete", delete_client, methods=["POST"]),
            Route("/clients/{client_id}/set-default", set_default_client, methods=["POST"]),
            Route("/clients/{client_id}/test/{platform}", test_connection, methods=["POST"]),
            Route("/tls", tls_page, methods=["GET"]),
            Route("/tls/generate", tls_generate, methods=["POST"]),
            Route("/tls/import", tls_import, methods=["POST"]),
        ]
    )


async def run_admin(clients_store: ClientsStore, port: int = 8787):
    """Launch the admin UI, bound to 127.0.0.1 only."""
    admin_token = secrets.token_urlsafe(24)
    app = create_admin_app(clients_store, admin_token)

    url = f"http://127.0.0.1:{port}/?token={admin_token}"
    logger.info("CentralMind admin UI starting (localhost only).")
    logger.info(f"Open this URL in your browser: {url}")

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
