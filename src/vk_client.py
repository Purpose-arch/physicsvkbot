"""
Надёжный клиент VK Long Poll, построенный напрямую на aiohttp.

Замена vkbottle: без тяжёлых зависимостей, с явной логикой повторов
и стабильным API. Использует тот же BOT_TOKEN из .env.
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
    """Ошибка VK API с числовым кодом (аналог VKAPIError из vkbottle)."""

    def __init__(self, code: int, message: str = ""):
        self.code = code
        self.message = message
        super().__init__(f"VK API error {code}: {message}")


@dataclass
class Message:
    """Лёгкий контекст сообщения, передаваемый обработчикам."""

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
        """Возвращает строки вложений вида 'photo123_456' из сырого обновления."""
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
    """Обёртка над VK API + слушатель Long Poll."""

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
        """Вызывает метод VK API с автоматическими повторами и backoff."""
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
                # 6 (слишком много запросов) и 10 (внутренняя ошибка сервера) — можно повторить
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
        """Отправляет сообщение; при отклонении повторяет один раз без вложения."""
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
            # Сообщение не доставлено: скорее всего вложение устарело
            # или отклонено VK. Повторяем один раз без него как fallback.
            logger.warning(f"VK rejected attachment '{attachment}' ({e}); retrying without it.")
            await self.call_method(
                "messages.send",
                peer_id=peer_id,
                message=text,
                random_id=random.randint(0, 2**31 - 1),
                **({"keyboard": keyboard} if keyboard else {}),
            )

    async def upload_message_photo(self, peer_id: int, image_path: str) -> str | None:
        """Загружает фото в VK и возвращает строку вложения для messages.send.

        Возвращает None при любой ошибке, чтобы вызывающая сторона могла
        отправить текстовый fallback вместо картинки.
        """
        import aiohttp

        try:
            upload_server = await self.call_method(
                "photos.getMessagesUploadServer", peer_id=peer_id
            )
            upload_url = upload_server.get("upload_url")
            if not upload_url:
                return None

            session = await self._get_session()
            with open(image_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("photo", f, filename="chart.png", content_type="image/png")
                async with session.post(upload_url, data=form) as resp:
                    uploaded = await resp.json()

            # VK upload-сервер исторически возвращает поля в разных обёртках:
            # непосредственно в объекте, в ``response`` или в первом элементе
            # списка. Приводим все варианты к одному объекту до save-метода.
            if isinstance(uploaded, list):
                uploaded = uploaded[0] if uploaded else {}
            if isinstance(uploaded, dict) and isinstance(uploaded.get("response"), (dict, list)):
                uploaded = uploaded["response"]
            if isinstance(uploaded, list):
                uploaded = uploaded[0] if uploaded else {}
            if not isinstance(uploaded, dict):
                logger.error("VK upload server returned an unexpected response type: %s", type(uploaded).__name__)
                return None

            photo = uploaded.get("photo")
            server = uploaded.get("server")
            file_hash = uploaded.get("hash")
            if not photo or not server or not file_hash:
                logger.error(
                    "VK upload server response is missing required fields "
                    "(keys: %s)",
                    sorted(uploaded.keys()),
                )
                return None

            saved = await self.call_method(
                "photos.saveMessagesPhoto",
                photo=photo,
                server=server,
                hash=file_hash,
            )
            if not saved:
                return None
            if isinstance(saved, dict):
                saved = [saved]
            photo = saved[0]
            return f"photo{photo['owner_id']}_{photo['id']}"
        except Exception as e:
            logger.error(f"Failed to upload message photo: {e}")
            return None

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
        """Ожидает следующее событие Long Poll. Возвращает сырой апдейт или None при сбое."""
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
            if failed in (1, 3):  # потеряна история / истёк ts — ключ ещё действителен для 1, 2
                self._lp_ts = data.get("ts", self._lp_ts)
                if failed == 3:
                    self._lp_server = None
            elif failed == 2:
                self._lp_server = None  # ключ истёк
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