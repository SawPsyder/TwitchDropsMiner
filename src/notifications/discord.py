"""
Discord notification provider.

Sends short embeds to a single channel of a Discord server (guild) that the
user's own bot has been invited to. TDM never ships a shared bot identity -
every user creates their own Discord Application/bot in the Discord Developer
Portal and pastes its token into settings; a shared, publicly-distributed bot
token could be extracted from the source/image and abused.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from src.notifications.base import NotificationError, NotificationProvider


logger = logging.getLogger("TwitchDrops")

API_BASE = "https://discord.com/api/v10"

# Send Messages (0x800) + Embed Links (0x4000) - the only permissions the bot needs
INVITE_PERMISSIONS = 0x800 | 0x4000

# embed side-bar colors per event type (decimal, Discord's embed "color" field)
EVENT_COLORS: dict[str, int] = {
    "drop_received": 0x9146FF,  # Twitch purple
    "unlinked_tracked_game": 0xF0A020,  # amber
    "auth_attention": 0xE02020,  # red
    "mining_stalled": 0xE0A020,  # orange
    "new_campaign": 0x20C060,  # green
}

# generic request timeout - these calls happen on user action (settings save,
# test button) or on mining events, never in a hot loop
REQUEST_TIMEOUT = 15


class DiscordProvider(NotificationProvider):
    """Sends notifications to a Discord channel via a user-owned bot token."""

    name = "discord"

    @property
    def bot_token(self) -> str:
        return str(self.provider_settings.get("bot_token", "")).strip()

    @property
    def guild_id(self) -> str:
        return str(self.provider_settings.get("guild_id", "")).strip()

    @property
    def channel_id(self) -> str:
        return str(self.provider_settings.get("channel_id", "")).strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token) and bool(self.channel_id)

    def _sensitive_values(self) -> tuple[str, ...]:
        return (self.bot_token,)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bot {self.bot_token}"}

    async def _request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Perform a Discord API request and return the parsed JSON body (or None)."""
        try:
            async with session.request(
                method, f"{API_BASE}{path}", headers=self._auth_headers(), json=json
            ) as response:
                if response.status in (401, 403):
                    raise NotificationError(
                        "Discord: bot token was rejected, or the bot is missing"
                        " permissions for this channel (401/403)"
                    )
                if response.status == 404:
                    raise NotificationError("Discord: server or channel not found (404)")
                if response.status == 429:
                    body = await response.json()
                    retry_after = body.get("retry_after", 1)
                    raise NotificationError(
                        f"Discord: rate limited, retry after {retry_after}s (429)"
                    )
                if response.status >= 400:
                    raise NotificationError(f"Discord: request failed ({response.status})")
                if response.status == 204:
                    return None
                return await response.json()
        except NotificationError:
            raise
        except Exception as exc:
            raise NotificationError(f"Discord: connection error: {self._redact(str(exc))}") from exc

    async def connect(self) -> dict[str, Any]:
        """
        Verify the configured bot token and build an invite link for it.

        Raises:
            NotificationError: If the token is missing/invalid.
        """
        if not self.bot_token:
            raise NotificationError("Discord: bot token must be configured")
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            bot_user = await self._request(session, "GET", "/users/@me")
            application = await self._request(session, "GET", "/oauth2/applications/@me")
        application_id = str(application.get("id", ""))
        invite_url = (
            f"https://discord.com/oauth2/authorize?client_id={application_id}"
            f"&scope=bot&permissions={INVITE_PERMISSIONS}"
        )
        username = bot_user.get("username", "unknown")
        return {
            "bot_username": username,
            "application_id": application_id,
            "invite_url": invite_url,
        }

    async def list_guilds(self) -> list[dict[str, str]]:
        """List guilds (servers) the bot has been invited to."""
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            guilds = await self._request(session, "GET", "/users/@me/guilds")
        return [{"id": str(guild["id"]), "name": str(guild.get("name", guild["id"]))} for guild in guilds]

    async def list_channels(self, guild_id: str) -> list[dict[str, str]]:
        """List text channels of a guild the bot can post in."""
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            channels = await self._request(session, "GET", f"/guilds/{guild_id}/channels")
        # type 0 == GUILD_TEXT (https://discord.com/developers/docs/resources/channel)
        return [
            {"id": str(channel["id"]), "name": str(channel.get("name", channel["id"]))}
            for channel in channels
            if channel.get("type") == 0
        ]

    async def send(self, event_type: str, title: str, description: str) -> None:
        if not self.is_configured:
            raise NotificationError("Discord: bot token and channel must be configured")
        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "color": EVENT_COLORS.get(event_type, 0x808080),
                }
            ]
        }
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            await self._request(
                session, "POST", f"/channels/{self.channel_id}/messages", json=payload
            )
        logger.info("Discord notification sent: %s", event_type)
