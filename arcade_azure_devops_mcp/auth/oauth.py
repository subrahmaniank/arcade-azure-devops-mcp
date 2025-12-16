"""OAuth/Azure AD authentication handler for Azure DevOps."""

import asyncio
from typing import Optional

from msal import ConfidentialClientApplication


class OAuthHandler:
    """Handles OAuth authentication with Azure AD for Azure DevOps."""

    # Azure DevOps resource scope for OAuth
    AZURE_DEVOPS_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tenant_id: str,
    ):
        """Initialize OAuth handler.

        Args:
            client_id: Azure AD application client ID.
            client_secret: Azure AD application client secret.
            tenant_id: Azure AD tenant ID.
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self._app: Optional[ConfidentialClientApplication] = None
        self._cached_token: Optional[str] = None

    def _get_msal_app(self) -> ConfidentialClientApplication:
        """Get or create MSAL confidential client application."""
        if self._app is None:
            authority = f"https://login.microsoftonline.com/{self.tenant_id}"
            self._app = ConfidentialClientApplication(
                client_id=self.client_id,
                client_credential=self.client_secret,
                authority=authority,
            )
        return self._app

    async def get_access_token(self) -> Optional[str]:
        """Get access token for Azure DevOps.

        Uses client credentials flow for service-to-service authentication.

        Returns:
            Access token string or None if acquisition fails.
        """
        app = self._get_msal_app()

        # Run token acquisition in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: app.acquire_token_for_client(scopes=[self.AZURE_DEVOPS_SCOPE]),
        )

        if result and "access_token" in result:
            self._cached_token = result["access_token"]
            return self._cached_token

        # Log error details for debugging
        if result and "error" in result:
            error = result.get("error", "Unknown error")
            error_desc = result.get("error_description", "No description")
            print(f"OAuth error: {error} - {error_desc}")

        return None

    def get_cached_token(self) -> Optional[str]:
        """Get cached token if available."""
        return self._cached_token

    def clear_cache(self) -> None:
        """Clear cached token."""
        self._cached_token = None
        self._app = None

