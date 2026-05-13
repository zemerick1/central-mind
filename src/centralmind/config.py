"""Configuration management for CentralMind server."""

import platform
import shutil
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseSettings):
    """Server configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Aruba Central API credentials
    central_base_url: str = Field(
        default="https://internal.api.central.arubanetworks.com",
        description="Aruba Central API base URL",
    )
    central_client_id: str = Field(default="", description="Aruba Central OAuth2 client ID")
    central_client_secret: str = Field(default="", description="Aruba Central OAuth2 client secret")

    # ClearPass API credentials
    clearpass_base_url: str = Field(
        default="https://clearpass.example.com/api",
        description="ClearPass API base URL",
    )
    clearpass_client_id: str = Field(default="", description="ClearPass OAuth2 client ID")
    clearpass_client_secret: str = Field(default="", description="ClearPass OAuth2 client secret")
    clearpass_verify_ssl: bool = Field(default=True, description="Verify SSL certificates for ClearPass")

    # Mist API credentials
    mist_apitoken: str = Field(default="", description="Mist API token")
    mist_host: str = Field(
        default="api.mist.com",
        description="Mist API host",
    )

    # SDC API credentials
    sdc_apitoken: str = Field(default="", description="SDC API token")
    sdc_host: str = Field(
        default="api.sdcloud.juniperclouds.net",
        description="SDC API host",
    )

    # UXI API credentials
    uxi_client_id: str = Field(default="", description="UXI OAuth2 client ID")
    uxi_client_secret: str = Field(default="", description="UXI OAuth2 client secret")
    uxi_host: str = Field(
        default="api.capenetworks.com",
        description="UXI API host",
    )
    uxi_verify_ssl: bool = Field(default=True, description="Verify SSL certificates for UXI")

    # AOS-CX credentials
    aoscx_username: str = Field(default="", description="AOS-CX administrator username")
    aoscx_password: str = Field(default="", description="AOS-CX administrator password")
    aoscx_verify_ssl: bool = Field(default=False, description="Verify SSL certificates for AOS-CX")

    # CentralMind settings
    centralmind_debug: bool = Field(
        default=False,
        description="Enable debug logging",
    )
    deno_path: Optional[str] = Field(
        default=None,
        description="Path to Deno binary",
    )
    # Safety: restrict which HTTP methods the execute tool can use
    # Default: read-only (GET only). Set to "read-write" or "all" for full access.
    # Options: "readonly" (GET only), "readwrite" (GET+POST+PUT+PATCH), "all" (includes DELETE)
    centralmind_api_mode: str = Field(
        default="readonly",
        description="API access mode: readonly (GET), readwrite (GET+POST+PUT+PATCH), all (includes DELETE)",
    )
    # Rate limiting: max sandbox executions per minute (0 = unlimited)
    centralmind_rate_limit: int = Field(
        default=30,
        description="Max sandbox executions per minute (0 = unlimited)",
    )
    # Max concurrent sandbox processes
    centralmind_max_concurrent: int = Field(
        default=5,
        description="Max concurrent Deno sandbox processes",
    )
    # BUG 8: Make spec path configurable
    centralmind_spec_path: Optional[str] = Field(
        default=None,
        description="Path to resolved OpenAPI spec JSON file",
    )
    # Runtime obfuscation: renames all API-specific terms in the spec so the
    # LLM has zero pre-trained knowledge of the API.  Proves code mode works
    # with any OpenAPI spec, not just ones the LLM was trained on.
    centralmind_obfuscate_api: bool = Field(
        default=False,
        description="Obfuscate API spec at runtime for zero-knowledge testing",
    )

    def __init__(self, **kwargs):
        """Initialize config and auto-detect Deno path if not provided."""
        super().__init__(**kwargs)
        
        deno_name = "deno.exe" if platform.system() == "Windows" else "deno"
        
        if self.deno_path:
            p = Path(self.deno_path)
            # If DENO_PATH points to a directory, look for the binary inside it
            if p.is_dir():
                candidate = p / deno_name
                if candidate.is_file():
                    self.deno_path = str(candidate)
                else:
                    raise ValueError(
                        f"DENO_PATH '{self.deno_path}' is a directory but does not contain '{deno_name}'."
                    )
            elif not p.is_file():
                # Maybe missing .exe on Windows
                with_ext = Path(str(p) + ".exe")
                if platform.system() == "Windows" and with_ext.is_file():
                    self.deno_path = str(with_ext)
                else:
                    raise ValueError(f"DENO_PATH '{self.deno_path}' does not exist.")
        else:
            # Auto-detect Deno from PATH or ~/.deno/bin/deno
            deno_in_path = shutil.which("deno")
            if deno_in_path:
                self.deno_path = deno_in_path
            else:
                home_deno = Path.home() / ".deno" / "bin" / deno_name
                if home_deno.exists():
                    self.deno_path = str(home_deno)
                else:
                    raise ValueError(
                        "Deno not found in PATH or ~/.deno/bin/deno. "
                        "Please install Deno or set DENO_PATH environment variable."
                    )

    @classmethod
    def load_from_env_file(cls, env_file: str) -> "ServerConfig":
        """Load configuration from a specific .env file."""
        return cls(_env_file=env_file)
