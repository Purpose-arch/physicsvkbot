"""
Reliable VK Long Poll client built directly on aiohttp.

Replaces vkbottle: no heavy dependencies, explicit retry logic,
stable API surface. Uses the same BOT_TOKEN from .env.
"""

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field

import aiohttp

from src.config import settings

logger = logging.getLogger(__name__)

API_VERSION = "5.131"
API_URL = "https://api.vk.com/method/"


class VKAPIError(Exception):
    """VK API error with numeric code (mirrors vkbottle's VKAPIError)."""

    def __init__(self, code: int, message: str = ""):
        self.code = code
        self.message = message
        super().__init__(f"VK API error {code}: {message}")


@dataclass
class Message:
    """Lightweight message context passed to handlers."""

    from_id: int
    peer_id: int
    text: str = ""
    payload: str | None = None
    _raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_update(cls, obj: dict) -> "Message":
        return cls(
            from_id=obj.get("from_id") or obj.get("user_id") or 0,
            peer_id=obj.get("peer_id") or obj.get("from_id") or 0,
            text=obj.get("text") or "",
            payload=obj.get("payload"),
            _raw=obj,
        )

    def get_attachment_strings(self) -> list[str]:
        """Return attachment strings like 'photo123_456' from raw update."""
        result: list[str] = []
        attachments = self._raw.get("attachments") or []
        if isinstance(attachments, dict):
            attachments = list(attachments.values())
        for att in attachments:
            if not isinstance(att, dict):
                continue
            att_type = att.get("type")
            att_obj = att.get(att_type)
            if not att_obj:
                continue
            owner_id = att_obj.get("owner_id")
            att_id = att_obj.get("id")
            access_key = att_obj.get("access_key") or ""
            if owner_id and att_id:
                result.append(
                    f"{att_type}{owner_id}_{att_id}_{access_key}"
                    if access_key
                    else f"{att_type}{owner_id}_{att_id}"
                )
        return result


class VKClient:
    """VK API wrapper + Long Poll listener."""

    def __init__(self, token: str):
        self.token = token
        self._session: aiohttp.ClientSession | None = None
        self._lp_server: str | None = None
        self._lp_key: str | None = None
        self._lp_ts: str | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            )
        return self._session

    async def call_method(self, method: str, **params) -> dict:
        """Call a VK API method with automatic retry/backoff."""
        params.setdefault("access_token", self.token)
        params.setdefault("v", API_VERSION)
        session = await self._get_session()

        backoff = 1
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                async with session.post(API_URL + method, data=params) as resp:
                    data = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_exc = e
                logger.warning(f"Network error calling {method} (attempt {attempt + 1}): {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue

            if "error" in data:
                err = data["error"]
                code = err.get("error_code", 0)
                # 6 (too many requests) and 10 (internal server error) — retryable
                if code in (6, 9, 10):
                    await asyncio.sleep(backoff + random.random())
                    backoff = min(backoff * 2, 30)
                    continue
                raise VKAPIError(code, err.get("error_msg", ""))
            return data.get("response", {})

        raise last_exc or VKAPIError(-1, f"Failed to call {method} after retries")

    async def send_message(
        self,
        peer_id: int,
        text: str,
        keyboard: str | None = None,
        attachment: str | None = None,
    ) -> None:
        """Send a message; retries once without attachment if rejected."""
        params = {"peer_id": peer_id, "message": text, "random_id": random.randint(0, 2**31 - 1)}
        if keyboard:
            params["keyboard"] = keyboard
        if attachment:
            params["attachment"] = attachment
        try:
            await self.call_method("messages.send", **params)
        except VKAPIError as e:
            if not attachment:
                raise
            # Message was not delivered; most likely the attachment is stale
            # or rejected by VK. Retry once without it as a fallback.
            logger.warning(f"VK rejected attachment '{attachment}' ({e}); retrying without it.")
            await self.call_method(
                "messages.send",
                peer_id=peer_id,
                message=text,
                random_id=random.randint(0, 2**31 - 1),
                **({"keyboard": keyboard} if keyboard else {}),
            )

    async def _refresh_longpoll_server(self) -> None:
        data = await self.call_method("groups.getLongPollServer", group_id=await self._get_group_id())
        self._lp_server = data["server"]
        self._lp_key = data["key"]
        self._lp_ts = data["ts"]

    async def _get_group_id(self) -> int:
        if not hasattr(self, "_group_id"):
            data = await self.call_method("groups.getById")
            group = data["groups"][0] if "groups" in data else data[0]
            self._group_id = group["id"]
        return self._group_id

    async def wait_event(self) -> dict | None:
        """Wait for the next Long Poll event. Returns raw update or None on failure."""
        session = await self._get_session()
        try:
            if not (self._lp_server and self._lp_key and self._lp_ts):
                await self._refresh_longpoll_server()

            url = f"{self._lp_server}?act=a_check&key={self._lp_key}&ts={self._lp_ts}&wait=25&mode=2&version=3"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
            logger.warning(f"Long Poll network error: {e}; refreshing server...")
            self._lp_server = None
            await asyncio.sleep(3)
            return None

        failed = data.get("failed")
        if failed:
            if failed in (1, 3):  # history lost / ts expired — key is still valid for 1, 2
                self._lp_ts = data.get("ts", self._lp_ts)
                if failed == 3:
                    self._lp_server = None
            elif failed == 2:
                self._lp_server = None  # key expired
            await asyncio.sleep(1)
            return None

        ts = data.get("ts")
        if ts is None:
            # Некорректный ответ сервера: сбрасываем сессию для переподключения
            logger.warning("Long Poll response missing 'ts'; refreshing server...")
            self._lp_server = None
            await asyncio.sleep(1)
            return None
        self._lp_ts = ts
        return data

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


vk_client = VKClient(token=settings.bot_token)