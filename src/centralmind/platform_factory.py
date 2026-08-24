"""Builds platform Auth instances from a credentials profile.

Shared by the MCP server (lazy, per-client auth construction) and the admin
web UI ("Test Connection" button — builds an Auth instance immediately to
validate credentials, then discards it without persisting anything beyond
what the user already saved).
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .auth import AoscxAuth, AxisAuth, CentralAuth, ClearpassAuth, MistAuth, SdcAuth, UxiAuth
from .clients_store import API_MODE_ORDER

logger = logging.getLogger(__name__)


def resolve_api_mode(server_ceiling: str, client_override: Optional[str]) -> str:
    """Resolve the effective API mode for a client.

    The server's own launch-time mode is a hard ceiling: a client's override
    can narrow access further but can never exceed what the server itself
    allows. An override that's unset or not a recognized mode falls back to
    the server's ceiling as-is.
    """
    ceiling_index = API_MODE_ORDER.index(server_ceiling) if server_ceiling in API_MODE_ORDER else 0
    if client_override not in API_MODE_ORDER:
        return API_MODE_ORDER[ceiling_index]
    override_index = API_MODE_ORDER.index(client_override)
    return API_MODE_ORDER[min(ceiling_index, override_index)]


@dataclass
class PlatformSpec:
    """Static, platform-wide (not per-client) properties needed to wire up
    the Deno sandbox and MCP tool schema for a platform."""

    auth_scheme: str
    verify_ssl_field: Optional[str] = None
    extra_params: Optional[Dict[str, Any]] = field(default=None)
    required_params: Optional[List[str]] = field(default=None)


PLATFORM_SPECS: Dict[str, PlatformSpec] = {
    "central": PlatformSpec(auth_scheme="Bearer"),
    "clearpass": PlatformSpec(auth_scheme="Bearer", verify_ssl_field="clearpass_verify_ssl"),
    "mist": PlatformSpec(auth_scheme="Token"),
    "axis": PlatformSpec(auth_scheme="Bearer"),
    "sdc": PlatformSpec(auth_scheme="x-api-key"),
    "uxi": PlatformSpec(auth_scheme="Bearer", verify_ssl_field="uxi_verify_ssl"),
    "aoscx": PlatformSpec(
        auth_scheme="aoscx-cookie",
        verify_ssl_field="aoscx_verify_ssl",
        extra_params={
            "switch_ip": {
                "type": "string",
                "description": "IP address or hostname of the AOS-CX switch",
            },
            "version": {
                "type": "string",
                "description": "API version to use (e.g., v10.13)",
                "default": "v10.13",
            },
        },
        required_params=["switch_ip"],
    ),
}


def build_platform_auth(platform: str, profile: Any):
    """Construct the Auth instance for `platform` from `profile`'s credential
    fields. Returns None if the profile has no credentials for this platform.
    Raises RuntimeError if credentials are present but authentication fails
    (network error, bad secret, etc.) — callers decide how to surface that.
    """
    if platform == "central":
        if profile.central_client_id and profile.central_client_secret:
            return CentralAuth(
                client_id=profile.central_client_id,
                client_secret=profile.central_client_secret,
                base_url=profile.central_base_url,
            )
    elif platform == "clearpass":
        if profile.clearpass_client_id and profile.clearpass_client_secret:
            return ClearpassAuth(
                client_id=profile.clearpass_client_id,
                client_secret=profile.clearpass_client_secret,
                base_url=profile.clearpass_base_url,
                verify_ssl=profile.clearpass_verify_ssl,
            )
    elif platform == "mist":
        if profile.mist_apitoken:
            return MistAuth(api_token=profile.mist_apitoken, host=profile.mist_host)
    elif platform == "axis":
        if profile.axis_apitoken:
            return AxisAuth(api_token=profile.axis_apitoken, host=profile.axis_host)
    elif platform == "sdc":
        if profile.sdc_apitoken:
            return SdcAuth(api_token=profile.sdc_apitoken, host=profile.sdc_host)
    elif platform == "uxi":
        if profile.uxi_client_id and profile.uxi_client_secret:
            return UxiAuth(
                client_id=profile.uxi_client_id,
                client_secret=profile.uxi_client_secret,
                host=profile.uxi_host,
                verify_ssl=profile.uxi_verify_ssl,
            )
    elif platform == "aoscx":
        if profile.aoscx_username and profile.aoscx_password:
            return AoscxAuth(
                username=profile.aoscx_username,
                password=profile.aoscx_password,
                verify_ssl=profile.aoscx_verify_ssl,
            )
    else:
        raise ValueError(f"Unknown platform: {platform}")

    return None
