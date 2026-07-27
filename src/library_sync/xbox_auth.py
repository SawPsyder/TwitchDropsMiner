"""
Xbox / Microsoft account authentication for the Xbox library provider.

Microsoft has no API key mechanism for reading a personal account's library, so
this authenticates the same way the app already authenticates against Twitch: an
OAuth device-code flow. The user opens a short URL, types an 8-character code,
and the app receives a long-lived refresh token which is persisted to
DATA_DIR/xbox_auth.json. Nothing is ever pasted into the settings, so no Xbox
credential lives in settings.json.

Reading an Xbox library then needs a three-step token chain, which is how every
Xbox Live client works:

1. MSA device code  -> Microsoft access + refresh token (login.live.com)
2. access token     -> Xbox "user token"  (user.auth.xboxlive.com)
3. user token       -> XSTS token         (xsts.auth.xboxlive.com)

Only the XSTS token can talk to the Xbox services, and it carries the account's
XUID, which the library endpoints are addressed by. XSTS tokens are valid for
several hours and are cached in memory only - the refresh token on disk is the
single piece of durable state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, NoReturn
from urllib.parse import quote

from src.config import XBOX_AUTH_PATH
from src.library_sync.base import LibrarySyncError
from src.utils import json_load, json_save


if TYPE_CHECKING:
    from pathlib import Path

    import aiohttp


logger = logging.getLogger("TwitchDrops")

DEVICE_CODE_URL = "https://login.live.com/oauth20_connect.srf"
TOKEN_URL = "https://login.live.com/oauth20_token.srf"
USER_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"

# Public client id used for the device-code flow. Paired with the MBI_SSL scope it
# yields an access token that user.auth.xboxlive.com accepts directly as an RPS
# ticket, which is why this legacy client is preferred over registering a new Entra
# app (that route needs the "XboxLive.signin" scope, a d= style ticket, and an app
# registration - and the community ids for it are gone from the consumers tenant).
#
# The id matters more than it looks: several Microsoft client ids happily *mint* a
# device code and then dead-end at the consent step with "the application is a first
# party application, the user does not have consent, and users are not permitted to
# consent to first party applications", which the code-entry page reports to the user
# as an incorrect code. 0000000048093EE3 (the Xbox app) is one of those - do not use
# it. Verified to get a normal account through the consent gate: 000000004C12AE6F
# (used here), 00000000441cc96b (Xbox Beta app), 00000000402b5328 (Minecraft
# launcher). If this one ever starts failing, swap in one of the other two.
CLIENT_ID = "000000004C12AE6F"
SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"

# Relying party for the Xbox Live services (titlehub, inventory). A different
# relying party would be needed for the Microsoft Store services, which is why
# tokens are cached per relying party rather than globally.
RELYING_PARTY_XBOXLIVE = "http://xboxlive.com"

# XSTS tokens are good for ~16h; renew early so a long-running sync never trips
# over one expiring mid-request
XSTS_LEEWAY = timedelta(minutes=10)

# XSTS refuses accounts that can't use Xbox Live, with the reason in an "XErr"
# code rather than a message. These are the ones worth explaining to the user.
XSTS_ERRORS = {
    2148916227: "this Microsoft account was banned from Xbox Live",
    2148916233: (
        "this Microsoft account has no Xbox profile -"
        " sign in at xbox.com once to create one, then try again"
    ),
    2148916235: "Xbox Live is not available in this account's region",
    2148916236: "this account needs adult verification (South Korea)",
    2148916237: "this account needs adult verification (South Korea)",
    2148916238: (
        "this is a child account without a family -"
        " add it to a Microsoft family group to use Xbox Live"
    ),
}

_EMPTY_AUTH_STATE: dict[str, Any] = {
    # long-lived MSA refresh token - the only durable credential
    "refresh_token": "",
    # cached so the UI can show which account is connected without a round trip
    "gamertag": "",
    "xuid": "",
}


class XboxLoginPending(LibrarySyncError):
    """
    Raised while the user hasn't finished entering the device code yet.

    slow_down is set when the token endpoint asked to be polled less often
    (RFC 8628); the caller has to widen its interval rather than treat it as a
    failure, or the sign-in dies for a reason the user can't see.
    """

    def __init__(self, message: str, *, slow_down: bool = False) -> None:
        super().__init__(message)
        self.slow_down = slow_down


@dataclass(frozen=True)
class DeviceCodePrompt:
    """What the user needs in order to approve the sign-in."""

    user_code: str
    verification_uri: str
    # same page with the code already in the box - see build_complete_uri
    verification_uri_complete: str
    expires_at: datetime
    interval: int
    device_code: str

    def as_dict(self) -> dict[str, Any]:
        """Serialize for the web GUI (the device code itself stays server-side)."""
        return {
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "verification_uri_complete": self.verification_uri_complete,
            "expires_at": self.expires_at.isoformat(),
        }


def build_complete_uri(verification_uri: str, user_code: str) -> str:
    """
    Add the code to the verification URL so the user never has to type it.

    Microsoft's device-code response carries no verification_uri_complete, but
    its code-entry page does accept the code as an "otc" query parameter and
    pre-fills the field with it (verified against the live page). That removes
    the single most likely way this flow fails: user codes are full of
    look-alike characters (5/S, 6/G, 0/O, 8/B), so a hand-typed code gets
    rejected as "that code is not correct" while the code itself was fine.
    """
    if not verification_uri or not user_code:
        return verification_uri
    separator = "&" if "?" in verification_uri else "?"
    return f"{verification_uri}{separator}otc={quote(user_code)}"


@dataclass(frozen=True)
class XstsToken:
    """An XSTS token plus the identity claims that come with it."""

    authorization: str  # ready-to-use "XBL3.0 x=<userhash>;<token>" header value
    xuid: str
    gamertag: str
    expires_at: datetime

    @property
    def expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at - XSTS_LEEWAY


class XboxAuth:
    """
    Owns the Microsoft/Xbox token chain and its persisted refresh token.

    The provider asks for an authorization header; everything behind it
    (device-code sign-in, refresh, the two token exchanges, caching) happens
    here.
    """

    def __init__(self, auth_path: Path = XBOX_AUTH_PATH) -> None:
        self._auth_path = auth_path
        self._auth: dict[str, Any] = self._load_auth(auth_path)
        self._pending: DeviceCodePrompt | None = None
        # XSTS tokens by relying party
        self._tokens: dict[str, XstsToken] = {}
        # why the last sign-in attempt ended, for the web GUI. Without this a failed
        # or abandoned approval is completely silent: the code just sits there.
        self._login_error: str = ""

    @staticmethod
    def _load_auth(auth_path: Path) -> dict[str, Any]:
        """
        Read the persisted sign-in, tolerating a damaged file.

        A truncated or hand-edited xbox_auth.json (an unclean container shutdown is
        enough) must not stop the app from starting - the worst case is that the
        account shows as disconnected and the user signs in again.
        """
        try:
            return json_load(auth_path, _EMPTY_AUTH_STATE, merge=False)
        except (ValueError, OSError) as exc:
            logger.warning(
                "Xbox library sync: ignoring unreadable %s (%s) - sign in again",
                auth_path.name,
                exc,
            )
            return dict(_EMPTY_AUTH_STATE)

    @property
    def refresh_token(self) -> str:
        return str(self._auth.get("refresh_token") or "")

    @property
    def signed_in(self) -> bool:
        """Whether a persisted refresh token exists to sync with."""
        return bool(self.refresh_token)

    @property
    def gamertag(self) -> str:
        return str(self._auth.get("gamertag") or "")

    @property
    def pending_prompt(self) -> DeviceCodePrompt | None:
        """The device-code prompt awaiting approval, if a sign-in is in flight."""
        if self._pending is not None and datetime.now(UTC) >= self._pending.expires_at:
            self._pending = None
            if not self.signed_in:
                # the overwhelmingly common "it didn't work" case: the code was never
                # approved. Say so instead of just making the prompt disappear.
                self._login_error = (
                    "the sign-in code expired before it was approved -"
                    " enter the code and complete the Microsoft sign-in, then try again"
                )
        return self._pending

    @property
    def last_login_error(self) -> str:
        """Why the last sign-in attempt ended, or "" if there's nothing to report."""
        return self._login_error

    def sensitive_values(self) -> tuple[str, ...]:
        """Credential values that must never reach logs or the web GUI."""
        values = [self.refresh_token]
        values.extend(token.authorization for token in self._tokens.values())
        return tuple(value for value in values if value)

    def sign_out(self) -> None:
        """Forget the account: drop the refresh token, cached tokens and prompt."""
        self._auth = dict(_EMPTY_AUTH_STATE)
        self._tokens.clear()
        self._pending = None
        self._login_error = ""
        json_save(self._auth_path, self._auth)

    def _save(self) -> None:
        json_save(self._auth_path, self._auth)

    async def _post_form(
        self,
        session: aiohttp.ClientSession,
        url: str,
        data: dict[str, str],
        proxy: str | None,
    ) -> tuple[int, dict[str, Any]]:
        """POST a form-encoded body and return (status, parsed JSON body)."""
        try:
            async with session.post(url, data=data, proxy=proxy) as response:
                # login.live.com reports pending/expired approvals as 400 with a
                # JSON error body, so the body matters even on failure statuses
                body: dict[str, Any] = await response.json(content_type=None)
                return response.status, body
        except Exception as exc:
            raise LibrarySyncError(f"Xbox: connection error: {exc}") from exc

    async def _post_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        payload: dict[str, Any],
        proxy: str | None,
    ) -> tuple[int, dict[str, Any]]:
        """POST a JSON body to an Xbox auth endpoint."""
        headers = {"x-xbl-contract-version": "1", "Accept": "application/json"}
        try:
            async with session.post(url, json=payload, headers=headers, proxy=proxy) as response:
                body: dict[str, Any] = await response.json(content_type=None)
                return response.status, body
        except Exception as exc:
            raise LibrarySyncError(f"Xbox: connection error: {exc}") from exc

    async def start_device_code(
        self, session: aiohttp.ClientSession, proxy: str | None = None
    ) -> DeviceCodePrompt:
        """
        Begin a device-code sign-in and return the code the user must enter.

        Raises:
            LibrarySyncError: If Microsoft refused to issue a device code
        """
        # a new attempt supersedes whatever the last one reported
        self._login_error = ""
        status, body = await self._post_form(
            session,
            DEVICE_CODE_URL,
            {"client_id": CLIENT_ID, "scope": SCOPE, "response_type": "device_code"},
            proxy,
        )
        if status >= 400 or not body.get("device_code"):
            self._fail_login(
                f"could not start sign-in ({status}):"
                f" {body.get('error_description') or body.get('error') or 'unknown error'}"
            )
        user_code = str(body["user_code"])
        # Microsoft returns https://www.microsoft.com/link, which redirects to the
        # remoteconnect code-entry page; fall back to that page directly
        verification_uri = str(
            body.get("verification_uri") or "https://login.live.com/oauth20_remoteconnect.srf"
        )
        self._pending = DeviceCodePrompt(
            user_code=user_code,
            verification_uri=verification_uri,
            # prefer a complete URI if Microsoft ever starts sending one
            verification_uri_complete=str(
                body.get("verification_uri_complete")
                or build_complete_uri(verification_uri, user_code)
            ),
            expires_at=datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in", 900))),
            interval=int(body.get("interval", 5)),
            device_code=str(body["device_code"]),
        )
        return self._pending

    async def poll_device_code(
        self, session: aiohttp.ClientSession, proxy: str | None = None
    ) -> bool:
        """
        Check once whether the pending device code has been approved.

        Returns:
            True when the sign-in completed and a refresh token was stored

        Raises:
            XboxLoginPending: The user hasn't entered the code yet
            LibrarySyncError: The code expired or was declined
        """
        prompt = self.pending_prompt
        if prompt is None:
            raise LibrarySyncError("Xbox: no sign-in is in progress")

        status, body = await self._post_form(
            session,
            TOKEN_URL,
            {
                "client_id": CLIENT_ID,
                "device_code": prompt.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            proxy,
        )
        if status == 200 and body.get("refresh_token"):
            self._auth["refresh_token"] = str(body["refresh_token"])
            self._pending = None
            self._tokens.clear()
            self._login_error = ""
            self._save()
            logger.info("Xbox library sync: account sign-in completed")
            return True

        error = str(body.get("error") or "")
        if error == "authorization_pending":
            raise XboxLoginPending("Xbox: waiting for the code to be entered")
        if error == "slow_down":
            # RFC 8628: keep polling, just less often. Treating this as fatal would
            # kill the sign-in while the user is still working through the prompts.
            raise XboxLoginPending("Xbox: asked to poll less often", slow_down=True)
        if error in ("authorization_declined", "access_denied"):
            self._pending = None
            self._fail_login("the sign-in was declined in the browser")
        if error in ("expired_token", "code_expired"):
            self._pending = None
            self._fail_login("the sign-in code expired - start again")
        self._fail_login(
            f"sign-in failed ({status}):"
            f" {body.get('error_description') or error or 'unknown error'}"
        )
        raise AssertionError("unreachable")  # pragma: no cover - _fail_login raises

    def _fail_login(self, reason: str) -> NoReturn:
        """Record why a sign-in ended and raise it, so the web GUI can show it."""
        self._login_error = reason
        raise LibrarySyncError(f"Xbox: {reason}")

    async def _refresh_access_token(
        self, session: aiohttp.ClientSession, proxy: str | None
    ) -> str:
        """Exchange the persisted refresh token for a fresh MSA access token."""
        if not self.signed_in:
            raise LibrarySyncError("Xbox: not signed in - connect your Microsoft account first")
        status, body = await self._post_form(
            session,
            TOKEN_URL,
            {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "scope": SCOPE,
            },
            proxy,
        )
        if status >= 400 or not body.get("access_token"):
            # a rejected refresh token is unrecoverable - make the user sign in again
            # instead of retrying it forever on every sync
            self.sign_out()
            self._fail_login("the stored sign-in expired or was revoked - sign in again")
        if body.get("refresh_token"):
            # Microsoft rotates refresh tokens; keep the newest one
            self._auth["refresh_token"] = str(body["refresh_token"])
            self._save()
        return str(body["access_token"])

    async def _fetch_user_token(
        self, session: aiohttp.ClientSession, access_token: str, proxy: str | None
    ) -> str:
        """Exchange an MSA access token for an Xbox Live user token."""
        payload = {
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"t={access_token}",
            },
        }
        status, body = await self._post_json(session, USER_AUTH_URL, payload, proxy)
        if status >= 400 or not body.get("Token"):
            raise LibrarySyncError(f"Xbox: Xbox Live rejected the account token ({status})")
        return str(body["Token"])

    @staticmethod
    def parse_xsts_response(body: dict[str, Any]) -> XstsToken:
        """
        Build an XstsToken from an /xsts/authorize response body.

        Raises:
            LibrarySyncError: If the response carries no usable token
        """
        token = body.get("Token")
        claims: list[dict[str, Any]] = (body.get("DisplayClaims") or {}).get("xui") or []
        if not token or not claims:
            raise LibrarySyncError("Xbox: authorization returned no token")
        claim = claims[0]
        user_hash = str(claim.get("uhs") or "")
        if not user_hash:
            raise LibrarySyncError("Xbox: authorization returned no user hash")
        not_after = body.get("NotAfter")
        try:
            expires_at = datetime.fromisoformat(str(not_after).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            # no/unparseable expiry - assume the documented 16h lifetime
            expires_at = datetime.now(UTC) + timedelta(hours=16)
        return XstsToken(
            authorization=f"XBL3.0 x={user_hash};{token}",
            xuid=str(claim.get("xid") or ""),
            gamertag=str(claim.get("gtg") or ""),
            expires_at=expires_at,
        )

    @staticmethod
    def _xsts_error(status: int, body: dict[str, Any]) -> LibrarySyncError:
        """Translate an XSTS rejection into something actionable."""
        xerr = body.get("XErr")
        if isinstance(xerr, int) and xerr in XSTS_ERRORS:
            return LibrarySyncError(f"Xbox: {XSTS_ERRORS[xerr]}")
        return LibrarySyncError(f"Xbox: Xbox Live authorization failed ({status})")

    async def authorization(
        self,
        session: aiohttp.ClientSession,
        proxy: str | None = None,
        relying_party: str = RELYING_PARTY_XBOXLIVE,
    ) -> XstsToken:
        """
        Return a usable XSTS token, running as much of the chain as needed.

        Cached tokens are reused until they near expiry, so a sync of several
        endpoints only authenticates once.

        Raises:
            LibrarySyncError: If any step of the token chain fails
        """
        cached = self._tokens.get(relying_party)
        if cached is not None and not cached.expired:
            return cached

        access_token = await self._refresh_access_token(session, proxy)
        user_token = await self._fetch_user_token(session, access_token, proxy)
        payload = {
            "RelyingParty": relying_party,
            "TokenType": "JWT",
            "Properties": {"UserTokens": [user_token], "SandboxId": "RETAIL"},
        }
        status, body = await self._post_json(session, XSTS_URL, payload, proxy)
        if status >= 400:
            raise self._xsts_error(status, body)

        token = self.parse_xsts_response(body)
        self._tokens[relying_party] = token
        # remember who's connected so the UI can name the account
        if token.xuid or token.gamertag:
            self._auth["xuid"] = token.xuid
            self._auth["gamertag"] = token.gamertag
            self._save()
        return token
