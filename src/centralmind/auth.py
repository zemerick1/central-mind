"""OAuth2 token management for Aruba Central API.

Handles the client_credentials grant flow and automatic token refresh.
Tokens are held in-memory for the server session lifetime — no disk I/O
after initial credential loading from environment variables.
"""

import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Buffer before expiry to trigger proactive refresh (5 minutes)
REFRESH_BUFFER_SECONDS = 300


class CentralAuth:
    """In-memory OAuth2 token manager for Aruba Central.

    On initialization, obtains an access token using the client_credentials
    grant type. Automatically refreshes the token before it expires.
    """

    def __init__(self, client_id: str, client_secret: str, base_url: str):
        """Initialize and obtain initial access token.

        Args:
            client_id: Aruba Central OAuth2 client ID
            client_secret: Aruba Central OAuth2 client secret
            base_url: Aruba Central API base URL (e.g., https://internal.api.central.arubanetworks.com)
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        # HPE GreenLake SSO is the OAuth2 provider for Aruba Central
        self.token_url = "https://sso.common.cloud.hpe.com/as/token.oauth2"

        self._access_token: Optional[str] = None
        self._token_expiry: float = 0.0  # Unix timestamp when token expires

        # Obtain initial token
        self._authenticate()

    def _authenticate(self) -> None:
        """Obtain access token via client_credentials grant."""
        logger.info("Authenticating with Aruba Central API...")

        try:
            response = httpx.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                timeout=30,
            )
            response.raise_for_status()
            token_data = response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Failed to authenticate with Aruba Central: "
                f"HTTP {e.response.status_code} — {e.response.text}"
            ) from e
        except httpx.RequestError as e:
            raise RuntimeError(
                f"Failed to connect to Aruba Central token endpoint "
                f"({self.token_url}): {e}"
            ) from e

        self._access_token = token_data.get("access_token")
        if not self._access_token:
            raise RuntimeError(
                f"Token response missing 'access_token': {token_data}"
            )

        # Calculate expiry (default 7200s / 2 hours if not specified)
        expires_in = token_data.get("expires_in", 7200)
        self._token_expiry = time.monotonic() + expires_in

        logger.info(
            f"Authenticated successfully. Token expires in {expires_in}s."
        )

    def _is_token_expired(self) -> bool:
        """Check if token is expired or about to expire."""
        return time.monotonic() >= (self._token_expiry - REFRESH_BUFFER_SECONDS)

    def get_token(self) -> str:
        """Return a valid access token, refreshing if needed.

        Returns:
            Current Bearer access token string
        """
        if self._is_token_expired():
            logger.info("Access token expired or expiring soon, refreshing...")
            self._authenticate()

        return self._access_token

    @property
    def host(self) -> str:
        """Extract hostname from base_url for Deno network allowlist."""
        from urllib.parse import urlparse
        parsed = urlparse(self.base_url)
        return parsed.hostname or self.base_url
