from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.google import GoogleTokenVerifier

from app.config import Settings
from app.mcp.auth import EmailAllowlistGoogleVerifier, build_google_auth, is_allowed, parse_allowlist


def _settings(
    *,
    google_client_id: str = "client-id",
    google_client_secret: str = "client-secret",
    mcp_base_url: str = "https://mcp.example.com",
    allowed_emails: str = "you@example.com",
    allowed_email_domains: str = "",
) -> Settings:
    return Settings(
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
        mcp_base_url=mcp_base_url,
        allowed_emails=allowed_emails,
        allowed_email_domains=allowed_email_domains,
    )


def _token(email: str | None, verified: bool | None = True) -> AccessToken:
    claims = {"email": email, "email_verified": verified}
    return AccessToken(token="t", client_id="sub-1", scopes=["openid"], claims=claims)


class TestParseAllowlist:
    def test_splits_lowercases_and_dedupes(self):
        assert parse_allowlist("A@x.com, a@x.com ,B@x.com") == {"a@x.com", "b@x.com"}

    def test_blank_yields_empty_set(self):
        assert parse_allowlist("") == frozenset()

    def test_strips_leading_at_when_requested(self):
        assert parse_allowlist("@Example.com, other.com", strip_leading_at=True) == {"example.com", "other.com"}


class TestIsAllowed:
    def test_rejects_unverified_email(self):
        assert not is_allowed({"email": "you@example.com", "email_verified": False}, {"you@example.com"}, set())

    def test_rejects_missing_email(self):
        assert not is_allowed({"email_verified": True}, {"you@example.com"}, set())

    def test_accepts_exact_email_match_case_insensitively(self):
        assert is_allowed({"email": "You@Example.com", "email_verified": True}, {"you@example.com"}, set())

    def test_accepts_domain_match(self):
        assert is_allowed({"email": "anyone@example.com", "email_verified": True}, set(), {"example.com"})

    def test_rejects_when_neither_list_matches(self):
        assert not is_allowed({"email": "you@other.com", "email_verified": True}, {"you@example.com"}, {"example.com"})


class TestEmailAllowlistGoogleVerifier:
    async def test_returns_none_when_base_verification_fails(self):
        verifier = EmailAllowlistGoogleVerifier(emails=frozenset({"you@example.com"}), domains=frozenset())
        with patch.object(GoogleTokenVerifier, "verify_token", AsyncMock(return_value=None)):
            assert await verifier.verify_token("bad-token") is None

    async def test_returns_none_when_verified_but_not_allowlisted(self):
        verifier = EmailAllowlistGoogleVerifier(emails=frozenset({"you@example.com"}), domains=frozenset())
        with patch.object(GoogleTokenVerifier, "verify_token", AsyncMock(return_value=_token("stranger@other.com"))):
            assert await verifier.verify_token("some-token") is None

    async def test_returns_access_token_when_allowlisted(self):
        verifier = EmailAllowlistGoogleVerifier(emails=frozenset({"you@example.com"}), domains=frozenset())
        expected = _token("you@example.com")
        with patch.object(GoogleTokenVerifier, "verify_token", AsyncMock(return_value=expected)):
            assert await verifier.verify_token("good-token") is expected


class TestBuildGoogleAuth:
    def test_raises_without_google_credentials(self):
        with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
            build_google_auth(_settings(google_client_id="", google_client_secret=""))

    def test_raises_without_base_url(self):
        with pytest.raises(RuntimeError, match="MCP_BASE_URL"):
            build_google_auth(_settings(mcp_base_url=""))

    def test_raises_without_allowlist(self):
        with pytest.raises(RuntimeError, match="allow-list"):
            build_google_auth(_settings(allowed_emails="", allowed_email_domains=""))

    def test_succeeds_with_domain_only_allowlist(self):
        auth = build_google_auth(_settings(allowed_emails="", allowed_email_domains="example.com"))
        assert isinstance(auth, OAuthProxy)

    def test_succeeds_when_fully_configured(self):
        assert isinstance(build_google_auth(_settings()), OAuthProxy)
