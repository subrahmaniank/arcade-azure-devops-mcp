"""Authentication manager for Azure DevOps with PAT and OAuth support."""

import base64
import os
from dataclasses import dataclass
from typing import Any, Optional


class AuthenticationError(Exception):
    """Raised when authentication fails or no valid credentials are configured."""

    pass


@dataclass
class AuthConfig:
    """Configuration for Azure DevOps authentication."""

    organization: str
    pat: Optional[str] = None
    oauth_client_id: Optional[str] = None
    oauth_client_secret: Optional[str] = None
    oauth_tenant_id: Optional[str] = None

    @classmethod
    def from_env(cls) -> "AuthConfig":
        """Create AuthConfig from environment variables."""
        org = os.environ.get("AZURE_DEVOPS_ORG")
        if not org:
            raise AuthenticationError(
                "AZURE_DEVOPS_ORG environment variable is required"
            )

        return cls(
            organization=org,
            pat=os.environ.get("AZURE_DEVOPS_PAT"),
            oauth_client_id=os.environ.get("AZURE_AD_CLIENT_ID"),
            oauth_client_secret=os.environ.get("AZURE_AD_CLIENT_SECRET"),
            oauth_tenant_id=os.environ.get("AZURE_AD_TENANT_ID"),
        )

    @classmethod
    def from_context(cls, context: Any) -> "AuthConfig":
        """Create AuthConfig from Arcade context secrets.
        
        Args:
            context: Arcade MCP context with get_secret method
        """
        # Try to get organization from context or env
        try:
            org = context.get_secret("AZURE_DEVOPS_ORG")
        except Exception:
            org = os.environ.get("AZURE_DEVOPS_ORG")
        
        if not org:
            raise AuthenticationError(
                "AZURE_DEVOPS_ORG not found in secrets or environment"
            )

        # Try to get PAT from context or env
        pat = None
        try:
            pat = context.get_secret("AZURE_DEVOPS_PAT")
        except Exception:
            pat = os.environ.get("AZURE_DEVOPS_PAT")

        return cls(
            organization=org,
            pat=pat,
        )

    @classmethod
    def from_env_or_context(cls, context: Optional[Any] = None) -> "AuthConfig":
        """Create AuthConfig from environment variables, falling back to Arcade context.
        
        Args:
            context: Optional Arcade MCP context with get_secret method
        """
        # First try environment variables
        org = os.environ.get("AZURE_DEVOPS_ORG")
        pat = os.environ.get("AZURE_DEVOPS_PAT")
        
        # If env vars are set, use them
        if org and pat:
            return cls(
                organization=org,
                pat=pat,
                oauth_client_id=os.environ.get("AZURE_AD_CLIENT_ID"),
                oauth_client_secret=os.environ.get("AZURE_AD_CLIENT_SECRET"),
                oauth_tenant_id=os.environ.get("AZURE_AD_TENANT_ID"),
            )
        
        # Fall back to Arcade context secrets
        if context is not None:
            try:
                if not org:
                    org = context.get_secret("AZURE_DEVOPS_ORG")
                if not pat:
                    pat = context.get_secret("AZURE_DEVOPS_PAT")
            except Exception:
                pass
        
        if not org:
            raise AuthenticationError(
                "AZURE_DEVOPS_ORG not found in environment or Arcade secrets"
            )

        return cls(
            organization=org,
            pat=pat,
        )


class AuthManager:
    """Manages authentication for Azure DevOps API requests.

    Supports both Personal Access Token (PAT) and OAuth/Azure AD authentication.
    PAT authentication is prioritized if available.
    """

    def __init__(self, config: Optional[AuthConfig] = None, context: Optional[Any] = None):
        """Initialize AuthManager with optional config.

        Args:
            config: Authentication configuration. If None, loads from environment or context.
            context: Optional Arcade MCP context for secrets fallback.
        """
        if config:
            self.config = config
        else:
            self.config = AuthConfig.from_env_or_context(context)
        self._oauth_token: Optional[str] = None

    @property
    def organization(self) -> str:
        """Get the Azure DevOps organization name."""
        return self.config.organization

    def get_headers(self) -> dict[str, str]:
        """Get authorization headers for API requests.

        Returns:
            Dictionary containing Authorization header.

        Raises:
            AuthenticationError: If no valid credentials are configured.
        """
        # Prioritize PAT authentication
        if self.config.pat:
            encoded = base64.b64encode(f":{self.config.pat}".encode()).decode()
            return {"Authorization": f"Basic {encoded}"}

        # Fall back to OAuth token if available
        if self._oauth_token:
            return {"Authorization": f"Bearer {self._oauth_token}"}

        raise AuthenticationError(
            "No valid credentials configured. Set AZURE_DEVOPS_PAT or configure Arcade secrets."
        )

    async def get_headers_async(self) -> dict[str, str]:
        """Get authorization headers, fetching OAuth token if needed.

        Returns:
            Dictionary containing Authorization header.

        Raises:
            AuthenticationError: If no valid credentials are configured.
        """
        # Prioritize PAT authentication
        if self.config.pat:
            encoded = base64.b64encode(f":{self.config.pat}".encode()).decode()
            return {"Authorization": f"Basic {encoded}"}

        raise AuthenticationError(
            "No valid credentials configured. Set AZURE_DEVOPS_PAT or configure Arcade secrets."
        )

    def has_valid_credentials(self) -> bool:
        """Check if valid credentials are available."""
        return bool(self.config.pat)
