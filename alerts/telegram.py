"""
Telegram Bot API client untuk RX-0 Unicorn — Phase 4.

Pakai httpx (sync, default) — ringan, no extra async runtime, no
python-telegram-bot overhead. Hanya butuh 1 endpoint:
    POST https://api.telegram.org/bot<TOKEN>/sendMessage

Graceful degradation:
    - TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID kosong -> instance dalam
      mode "degraded", send_message() log ke console dan return False
      tanpa crash. Memungkinkan daemon jalan untuk development tanpa
      bot sungguhan.

Token & chat id dibaca dari os.environ pada constructor (bukan import-time)
sehingga test bisa patch env vars dengan aman.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import httpx
except ImportError as exc:  # pragma: no cover - hard dep, should never hit
    raise ImportError(
        "httpx wajib di-install untuk Telegram alerts: pip install httpx"
    ) from exc

from src.logger import logger


class TelegramBot:
    """
    Thin wrapper untuk Telegram Bot API.

    Attributes:
        token: Bot token dari @BotFather. Kosong = degraded mode.
        chat_id: Target chat/user/group ID. Kosong = degraded mode.
        timeout: HTTP timeout dalam detik (default 10).
    """

    API_BASE: str = "https://api.telegram.org"

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        # Resolve di constructor (bukan import-time) supaya test bisa patch env.
        self.token: str = (token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id: str = (chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        self.timeout: float = timeout
        self._client: httpx.Client | None = None
        self.is_configured: bool = bool(self.token and self.chat_id)

        if self.is_configured:
            logger.debug(
                f"TelegramBot configured (chat_id={self.chat_id[:4]}***)"
            )
        else:
            logger.warning(
                "TelegramBot not configured (token/chat_id kosong) "
                "-> mode degraded, alert akan dicetak ke console."
            )

    # -- internal helpers -------------------------------------------------

    def _get_client(self) -> httpx.Client:
        """Lazy-init httpx client (reused per process)."""
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def _url(self) -> str:
        return f"{self.API_BASE}/bot{self.token}/sendMessage"

    # -- public API -------------------------------------------------------

    def send_message(
        self,
        text: str,
        parse_mode: str | None = None,
        disable_notification: bool = False,
    ) -> bool:
        """
        Kirim `text` ke chat_id. Return True kalau Telegram menerima
        (response.ok), False kalau degraded / network error / API error.

        Args:
            text: Body pesan (markdown / plain). Telegram limit 4096 char;
                  caller bertanggung jawab truncate kalau perlu.
            parse_mode: "Markdown", "MarkdownV2", "HTML", atau None (plain).
            disable_notification: True = silent message (no sound).

        Returns:
            True kalau sukses, False kalau gagal atau degraded mode.
        """
        if not self.is_configured:
            logger.info(
                "[telegram:degraded] (no token/chat_id) — would send:\n" + text
            )
            return False

        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_notification": disable_notification,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            client = self._get_client()
            resp = client.post(self._url(), json=payload)
        except httpx.HTTPError as exc:
            logger.error(f"Telegram send HTTP error: {exc}")
            return False
        except Exception as exc:  # noqa: BLE001 — defensive, log everything
            logger.error(f"Telegram send unexpected error: {exc}")
            return False

        if resp.status_code != 200:
            # Telegram balikin error JSON bahkan untuk 4xx; log body biar debuggable
            logger.error(
                f"Telegram API non-200 ({resp.status_code}): {resp.text[:300]}"
            )
            return False

        try:
            body = resp.json()
        except ValueError:
            logger.error("Telegram API returned non-JSON body")
            return False

        if not body.get("ok", False):
            logger.error(
                f"Telegram API returned ok=False: {body.get('description', body)}"
            )
            return False

        logger.debug("Telegram send_message OK")
        return True

    def close(self) -> None:
        """Tutup httpx client. Aman dipanggil多次 (idempotent)."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    def __enter__(self) -> "TelegramBot":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
