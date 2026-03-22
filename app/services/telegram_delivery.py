"""Telegram message delivery with retry/backoff."""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_RETRIES = 3
_RETRY_DELAYS = (1.0, 3.0, 8.0)  # seconds between retries


def send_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    parse_mode: str = "MarkdownV2",
    disable_web_page_preview: bool = True,
    retries: int = _DEFAULT_RETRIES,
) -> bool:
    """Send a single Telegram message. Returns True on success."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    for attempt in range(max(1, retries)):
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            if resp.status_code == 429:
                # Rate-limited — wait longer and retry
                retry_after = float(resp.json().get("parameters", {}).get("retry_after", 5))
                logger.warning("Telegram 429 rate limit, retry_after=%.0fs", retry_after)
                time.sleep(retry_after)
                continue
            if resp.status_code in (400, 403):
                # Bad request or bot blocked — no point retrying
                logger.warning(
                    "Telegram delivery failed chat_id=%s status=%s body=%s",
                    chat_id,
                    resp.status_code,
                    resp.text[:200],
                )
                return False
        except Exception:  # noqa: BLE001
            logger.exception("Telegram send error attempt=%d chat_id=%s", attempt + 1, chat_id)

        if attempt < retries - 1:
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            time.sleep(delay)

    return False


def broadcast(
    token: str,
    messages: list[dict],
    *,
    parse_mode: str = "MarkdownV2",
) -> int:
    """
    Send multiple messages to (potentially different) chat IDs.

    Each entry in `messages` must have:
        - "chat_id": str
        - "text": str
        - optionally "parse_mode": str (overrides default)
        - optionally "disable_web_page_preview": bool

    Returns number of successfully delivered messages.
    """
    sent = 0
    for msg in messages:
        chat_id = str(msg.get("chat_id") or "")
        text = str(msg.get("text") or "")
        if not chat_id or not text:
            continue
        pm = str(msg.get("parse_mode") or parse_mode)
        dwp = bool(msg.get("disable_web_page_preview", True))
        ok = send_message(token, chat_id, text, parse_mode=pm, disable_web_page_preview=dwp)
        if ok:
            sent += 1
    return sent


def send_to_chat(
    token: str,
    chat_id: str,
    messages: list[str],
    *,
    parse_mode: str = "MarkdownV2",
) -> int:
    """Send multiple text messages to a single chat. Returns count sent."""
    sent = 0
    for text in messages:
        if send_message(token, chat_id, text, parse_mode=parse_mode):
            sent += 1
    return sent
