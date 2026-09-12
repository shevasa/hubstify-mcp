from collections.abc import Container

from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.google import GoogleTokenVerifier

from app.config import Settings

_UPSTREAM_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_UPSTREAM_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_REQUIRED_SCOPES = ["openid", "email"]


class EmailAllowlistGoogleVerifier(GoogleTokenVerifier):
    """Google token verifier that also enforces an email/domain allow-list.

    Google OAuth proves the caller owns *some* Google account; it does not prove
    they're the person this server's single Hubstaff token belongs to. Every
    signed-in session shares that one Hubstaff account, so a Google login that
    isn't on the allow-list must be rejected outright, not merely authenticated.
    Returning None (both here and from the base verifier) is what the MCP SDK's
    bearer-auth middleware treats as an invalid token -> 401; there is no path
    that falls back to anonymous or partial access.
    """

    def __init__(self, *, emails: frozenset[str], domains: frozenset[str], **kwargs):
        super().__init__(**kwargs)
        self._emails = emails
        self._domains = domains

    async def verify_token(self, token: str) -> AccessToken | None:
        access_token = await super().verify_token(token)
        if access_token is None or not is_allowed(access_token.claims, self._emails, self._domains):
            return None
        return access_token


def is_allowed(claims: dict, emails: Container[str], domains: Container[str]) -> bool:
    """Whether a verified Google identity's claims match the allow-list.

    An unverified email address is never trusted for authorization, even if it
    happens to match an allow-listed value.
    """
    if not claims.get("email_verified"):
        return False
    email = (claims.get("email") or "").lower()
    if not email or "@" not in email:
        return False
    if email in emails:
        return True
    domain = email.rsplit("@", 1)[-1]
    return domain in domains


def parse_allowlist(raw: str, *, strip_leading_at: bool = False) -> frozenset[str]:
    """Parse a comma-separated allow-list entry into a lowercased set."""
    values = set()
    for part in raw.split(","):
        value = part.strip().lower()
        if strip_leading_at:
            value = value.removeprefix("@")
        if value:
            values.add(value)
    return frozenset(values)


def build_google_auth(settings: Settings) -> OAuthProxy:
    """Build the Google-OAuth auth provider for HTTP transport, gated by an allow-list.

    Raises RuntimeError if credentials, a base URL, or an allow-list are missing.
    Refusing to start is safer than silently exposing this server's one Hubstaff
    token to any Google account that completes the OAuth flow.
    """
    if not settings.google_client_id or not settings.google_client_secret:
        raise RuntimeError(
            "MCP_TRANSPORT=http requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET. Create an "
            "OAuth 2.0 Web application client at https://console.cloud.google.com/apis/credentials."
        )
    if not settings.mcp_base_url:
        raise RuntimeError(
            "MCP_TRANSPORT=http requires MCP_BASE_URL: the public HTTPS URL this server is "
            "reachable at (e.g. your Railway domain), used to build the OAuth redirect."
        )
    emails = parse_allowlist(settings.allowed_emails)
    domains = parse_allowlist(settings.allowed_email_domains, strip_leading_at=True)
    if not emails and not domains:
        raise RuntimeError(
            "MCP_TRANSPORT=http requires an authorization allow-list: set ALLOWED_EMAILS "
            "and/or ALLOWED_EMAIL_DOMAINS. Google sign-in only proves a caller owns a Google "
            "account, not that they're authorized for this server's Hubstaff token."
        )
    verifier = EmailAllowlistGoogleVerifier(emails=emails, domains=domains, required_scopes=_REQUIRED_SCOPES)
    return OAuthProxy(
        upstream_authorization_endpoint=_UPSTREAM_AUTHORIZATION_ENDPOINT,
        upstream_token_endpoint=_UPSTREAM_TOKEN_ENDPOINT,
        upstream_client_id=settings.google_client_id,
        upstream_client_secret=settings.google_client_secret,
        token_verifier=verifier,
        base_url=settings.mcp_base_url,
        issuer_url=settings.mcp_base_url,
        # access_type=offline + prompt=consent ensure Google returns a refresh
        # token, matching what GoogleProvider itself sets by default.
        extra_authorize_params={"access_type": "offline", "prompt": "consent"},
    )
