"""Encrypted multi-client credential store for connecting to multiple client environments.

Each "client" is one environment's set of platform credentials (Central,
ClearPass, Mist, Axis, SDC, UXI, AOS-CX). Profiles are persisted as a single
Fernet-encrypted JSON blob on disk so an engineer can manage credentials for
many client environments from one CentralMind instance without hand-editing
`.env` per switch.
"""

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_CLIENTS_DIR = Path.home() / ".centralmind"
DEFAULT_CLIENTS_FILE = DEFAULT_CLIENTS_DIR / "clients.json"
DEFAULT_KEY_FILE = DEFAULT_CLIENTS_DIR / "secret.key"

# API access mode ordering, least to most permissive. A client's own
# api_mode (see ClientProfile below) can only ever narrow this — the
# server's own launch-time CENTRALMIND_API_MODE is a hard ceiling no
# client override can exceed. See platform_factory.resolve_api_mode.
API_MODE_ORDER = ["readonly", "readwrite", "all"]

# Declarative per-platform field groups. Shared by the admin UI (to render
# forms without duplicating one per platform) and by list_clients (to report
# which platforms a profile has credentials for without instantiating auth).
# Each field tuple is (attribute_name, label, is_secret).
PLATFORM_FIELD_GROUPS: Dict[str, List[tuple]] = {
    "central": [
        ("central_base_url", "Base URL", False),
        ("central_client_id", "OAuth2 Client ID", False),
        ("central_client_secret", "OAuth2 Client Secret", True),
    ],
    "clearpass": [
        ("clearpass_base_url", "Base URL", False),
        ("clearpass_client_id", "OAuth2 Client ID", False),
        ("clearpass_client_secret", "OAuth2 Client Secret", True),
        ("clearpass_verify_ssl", "Verify SSL", False),
    ],
    "mist": [
        ("mist_apitoken", "API Token", True),
        ("mist_host", "API Host", False),
    ],
    "axis": [
        ("axis_apitoken", "API Token", True),
        ("axis_host", "API Host", False),
    ],
    "sdc": [
        ("sdc_apitoken", "API Token", True),
        ("sdc_host", "API Host", False),
    ],
    "uxi": [
        ("uxi_client_id", "OAuth2 Client ID", False),
        ("uxi_client_secret", "OAuth2 Client Secret", True),
        ("uxi_host", "API Host", False),
        ("uxi_verify_ssl", "Verify SSL", False),
    ],
    "aoscx": [
        ("aoscx_username", "Administrator Username", False),
        ("aoscx_password", "Administrator Password", True),
        ("aoscx_verify_ssl", "Verify SSL", False),
    ],
}

# Platforms whose required (non-verify_ssl, non-host-default) fields are all
# populated when a profile is considered "configured" for that platform.
_PLATFORM_REQUIRED_FIELDS: Dict[str, List[str]] = {
    "central": ["central_client_id", "central_client_secret"],
    "clearpass": ["clearpass_client_id", "clearpass_client_secret"],
    "mist": ["mist_apitoken"],
    "axis": ["axis_apitoken"],
    "sdc": ["sdc_apitoken"],
    "uxi": ["uxi_client_id", "uxi_client_secret"],
    "aoscx": ["aoscx_username", "aoscx_password"],
}


class ClientProfile(BaseModel):
    """One client environment's full set of platform credentials."""

    id: str
    name: str
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    # None = inherit the server's own launch-time CENTRALMIND_API_MODE.
    # A non-None value can only narrow access further, never widen it
    # beyond what the server itself allows — see platform_factory.resolve_api_mode.
    api_mode: Optional[str] = None

    # Aruba Central
    central_base_url: str = "https://internal.api.central.arubanetworks.com"
    central_client_id: str = ""
    central_client_secret: str = ""

    # ClearPass
    clearpass_base_url: str = "https://clearpass.example.com/api"
    clearpass_client_id: str = ""
    clearpass_client_secret: str = ""
    clearpass_verify_ssl: bool = True

    # Mist
    mist_apitoken: str = ""
    mist_host: str = "api.mist.com"

    # Axis Security
    axis_apitoken: str = ""
    axis_host: str = "admin-api.axissecurity.com"

    # SDC
    sdc_apitoken: str = ""
    sdc_host: str = "api.sdcloud.juniperclouds.net"

    # UXI
    uxi_client_id: str = ""
    uxi_client_secret: str = ""
    uxi_host: str = "api.capenetworks.com"
    uxi_verify_ssl: bool = True

    # AOS-CX
    aoscx_username: str = ""
    aoscx_password: str = ""
    aoscx_verify_ssl: bool = False

    def configured_platforms(self) -> List[str]:
        """Return the platform keys this profile has credentials for."""
        configured = []
        for platform, fields in _PLATFORM_REQUIRED_FIELDS.items():
            if all(getattr(self, field) for field in fields):
                configured.append(platform)
        return configured


class ClientsStore:
    """Encrypted-at-rest store of :class:`ClientProfile` objects.

    The whole JSON document is encrypted with Fernet (symmetric, authenticated
    encryption) using a key generated on first use and stored in a sibling
    file. Both files live under the user's home directory by default so a
    single CentralMind instance can hold many client environments' secrets
    without leaving them as plaintext on disk.
    """

    def __init__(self, path: Optional[Path] = None, key_path: Optional[Path] = None):
        env_path = os.environ.get("CENTRALMIND_CLIENTS_FILE")
        self.path = Path(path or env_path or DEFAULT_CLIENTS_FILE)
        self.key_path = Path(key_path or self.path.parent / "secret.key")
        self._fernet = self._load_or_create_key()
        self._data: Dict[str, Any] = self._load()

    def _load_or_create_key(self) -> Fernet:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            self.key_path.write_bytes(key)
            if os.name != "nt":
                os.chmod(self.key_path, 0o600)
            logger.info(f"Generated new client-store encryption key at {self.key_path}")
        return Fernet(key)

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"default_client_id": None, "api_key": None, "clients": {}}
        raw = self.path.read_bytes()
        try:
            decrypted = self._fernet.decrypt(raw)
        except InvalidToken as e:
            raise RuntimeError(
                f"Failed to decrypt {self.path} with key {self.key_path}. "
                "The key file may be missing or mismatched with the data file."
            ) from e
        return json.loads(decrypted.decode("utf-8"))

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data).encode("utf-8")
        encrypted = self._fernet.encrypt(payload)
        self.path.write_bytes(encrypted)
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list(self) -> List[ClientProfile]:
        return [ClientProfile(**c) for c in self._data["clients"].values()]

    def get(self, client_id: str) -> Optional[ClientProfile]:
        raw = self._data["clients"].get(client_id)
        return ClientProfile(**raw) if raw else None

    def save(self, profile: ClientProfile) -> ClientProfile:
        profile.updated_at = time.time()
        self._data["clients"][profile.id] = json.loads(profile.model_dump_json())
        if self._data.get("default_client_id") is None:
            self._data["default_client_id"] = profile.id
        self._persist()
        return profile

    def create(self, name: str, **fields: Any) -> ClientProfile:
        profile = ClientProfile(id=str(uuid.uuid4()), name=name, **fields)
        return self.save(profile)

    def delete(self, client_id: str) -> None:
        self._data["clients"].pop(client_id, None)
        if self._data.get("default_client_id") == client_id:
            remaining = list(self._data["clients"].keys())
            self._data["default_client_id"] = remaining[0] if remaining else None
        self._persist()

    def get_default_id(self) -> Optional[str]:
        default_id = self._data.get("default_client_id")
        if default_id and default_id in self._data["clients"]:
            return default_id
        remaining = list(self._data["clients"].keys())
        return remaining[0] if remaining else None

    def set_default(self, client_id: str) -> None:
        if client_id not in self._data["clients"]:
            raise KeyError(f"Unknown client id: {client_id}")
        self._data["default_client_id"] = client_id
        self._persist()

    # ------------------------------------------------------------------
    # Server-wide API key (for HTTP transport auth), stored alongside clients
    # ------------------------------------------------------------------

    def get_api_key(self) -> Optional[str]:
        return self._data.get("api_key")

    def set_api_key(self, api_key: str) -> None:
        self._data["api_key"] = api_key
        self._persist()

    # ------------------------------------------------------------------
    # Server-wide API mode ceiling override.
    #
    # None (the default) means "no override" — the running server uses
    # whatever CENTRALMIND_API_MODE it was launched with (or "readonly" if
    # that's unset). Setting this here lets that ceiling be changed from the
    # admin UI instead of editing .env, but it is read once at server
    # startup — see __main__.py — so it only takes effect the next time the
    # actual MCP server process is (re)started, not immediately.
    # ------------------------------------------------------------------

    def get_server_api_mode(self) -> Optional[str]:
        return self._data.get("server_api_mode")

    def set_server_api_mode(self, mode: Optional[str]) -> None:
        self._data["server_api_mode"] = mode
        self._persist()

    # ------------------------------------------------------------------
    # Migration from legacy single-tenant .env / environment config
    # ------------------------------------------------------------------

    def migrate_from_env(self, config: Any) -> Optional[ClientProfile]:
        """Create a "default" client from legacy env-var config, once.

        Only runs when the store has zero clients yet. Lets existing
        single-tenant users keep using their `.env` file unmodified after
        upgrading — their credentials become client "default" automatically.
        """
        if self._data["clients"]:
            return None

        field_names = [f for group in PLATFORM_FIELD_GROUPS.values() for f, _, _ in group]
        values = {f: getattr(config, f, None) for f in field_names if hasattr(config, f)}
        values = {k: v for k, v in values.items() if v not in (None, "")}
        if not values:
            return None

        logger.info(
            f"No client store found at {self.path} — migrating legacy .env "
            "credentials into a new 'default' client."
        )
        profile = ClientProfile(id="default", name="default", **values)
        return self.save(profile)

    def is_empty(self) -> bool:
        return not self._data["clients"]
