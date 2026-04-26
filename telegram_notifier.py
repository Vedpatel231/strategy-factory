"""
Telegram Bot Notifier — sends trading reports and alerts.

Env vars (set on Railway, NEVER commit actual values):
    TELEGRAM_BOT_TOKEN  — Bot API token from @BotFather
    TELEGRAM_CHAT_ID    — Your personal chat ID
"""

import os
import logging
import requests

logger = logging.getLogger("telegram_notifier")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

MAX_MESSAGE_LENGTH = 4096  # Telegram's limit per message


def is_configured():
    """Return True if Telegram credentials are set."""
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_message(text, chat_id=None, parse_mode=None):
    """Send a text message via Telegram Bot API.

    Args:
        text: Message content (max 4096 chars per message; auto-splits if longer)
        chat_id: Override chat ID (defaults to TELEGRAM_CHAT_ID env var)
        parse_mode: 'HTML', 'Markdown', or None for plain text

    Returns:
        True if all message chunks sent successfully, False otherwise.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — skipping notification")
        return False

    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not target_chat:
        logger.warning("TELEGRAM_CHAT_ID not set — skipping notification")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Split long messages
    chunks = _split_message(text)
    all_ok = True

    for chunk in chunks:
        payload = {
            "chat_id": target_chat,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                logger.info(f"Telegram message sent ({len(chunk)} chars)")
            else:
                logger.error(f"Telegram API error {resp.status_code}: {resp.text}")
                all_ok = False
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            all_ok = False

    return all_ok


def send_daily_report(report_text):
    """Send the daily analysis report, formatted for Telegram readability."""
    if not is_configured():
        logger.info("Telegram not configured — daily report not sent")
        return False

    # Add a header emoji for easy scanning in chat
    header = "📊 DAILY TRADING REPORT\n\n"
    return send_message(header + report_text)


def send_alert(alert_text, level="warning"):
    """Send an urgent alert (e.g., drawdown breaker, system error)."""
    if not is_configured():
        return False

    emoji = {"critical": "🚨", "warning": "⚠️", "info": "��️"}.get(level, "⚠️")
    return send_message(f"{emoji} TRADING ALERT\n\n{alert_text}")


def _split_message(text):
    """Split text into chunks that fit Telegram's 4096-char limit.
    Tries to split on newlines to keep formatting clean."""
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= MAX_MESSAGE_LENGTH:
            chunks.append(remaining)
            break

        # Find a good split point (newline near the limit)
        split_at = remaining.rfind('\n', 0, MAX_MESSAGE_LENGTH)
        if split_at < MAX_MESSAGE_LENGTH // 2:
            # No good newline — hard split
            split_at = MAX_MESSAGE_LENGTH

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip('\n')

    return chunks


# ── Quick test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    try:
        import env_loader  # noqa: F401
    except ImportError:
        pass

    if is_configured():
        ok = send_message("✅ Telegram notifier test — connection working!")
        print(f"Test message sent: {ok}")
    else:
        print("Not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.")
