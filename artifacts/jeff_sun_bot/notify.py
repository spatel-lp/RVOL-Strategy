"""
Optional alerts: Telegram and/or email, configured ONLY through environment variables (GitHub Actions secrets).
Nothing is sent, and nothing breaks, when they are not set. Sending never raises.

  Telegram: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  Email   : SMTP_HOST, SMTP_PORT (default 587, STARTTLS), SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO, [ALERT_EMAIL_FROM]
  Filter  : NOTIFY_ONLY=ENTRY,STOP,CLOSE,ERROR,DIGEST,WARN  (default: all)

CLI:  python notify.py --test           send a test message
      python notify.py "job failed"     send a one-off message (used by the workflow's failure step)
"""

import json
import os
import smtplib
import sys
import urllib.request
from email.message import EmailMessage
from typing import List, Optional, Tuple

from config import NOTIFY_KINDS


def configured() -> List[str]:
    out = []
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        out.append("telegram")
    if os.environ.get("SMTP_HOST") and os.environ.get("ALERT_EMAIL_TO"):
        out.append("email")
    return out


def _post_json(url: str, payload: dict, timeout: int = 10) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def telegram(text: str) -> Optional[str]:
    try:
        token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
        _post_json(f"https://api.telegram.org/bot{token}/sendMessage",
                   {"chat_id": chat, "text": text[:3900], "disable_web_page_preview": True})
        return None
    except Exception as e:
        return f"telegram: {type(e).__name__}"


def email(subject: str, body: str) -> Optional[str]:
    try:
        msg = EmailMessage()
        msg["Subject"], msg["To"] = subject, os.environ["ALERT_EMAIL_TO"]
        msg["From"] = os.environ.get("ALERT_EMAIL_FROM") or os.environ.get("SMTP_USER") or os.environ["ALERT_EMAIL_TO"]
        msg.set_content(body)
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
            s.send_message(msg)
        return None
    except Exception as e:
        return f"email: {type(e).__name__}"


def send(subject: str, body: str) -> dict:
    sent, errors = [], []
    for ch in configured():
        err = telegram(f"{subject}\n{body}") if ch == "telegram" else email(subject, body)
        (errors if err else sent).append(err or ch)
    return {"sent": sent, "errors": errors}


class Notifier:
    """Collects events during a job and sends ONE message at the end (no spam on a busy day)."""

    def __init__(self, enabled_kinds: Optional[Tuple[str, ...]] = None):
        only = os.environ.get("NOTIFY_ONLY")
        self.kinds = tuple(k.strip().upper() for k in only.split(",")) if only else (enabled_kinds or NOTIFY_KINDS)
        self.items: List[Tuple[str, str]] = []

    def add(self, kind: str, msg: str) -> None:
        if kind in self.kinds:
            self.items.append((kind, msg))

    def flush(self, title: str) -> dict:
        if not self.items:
            return {"sent": [], "errors": []}
        body = "\n".join(f"[{k}] {m}" for k, m in self.items)
        self.items = []
        return send(title, body)


if __name__ == "__main__":
    text = "paper-trading: test message" if "--test" in sys.argv else " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
    if not configured():
        print("no channel configured (set TELEGRAM_* or SMTP_* environment variables)")
        sys.exit(0)
    res = send("paper-trading", text or "ping")
    print(res)
    sys.exit(1 if res["errors"] and not res["sent"] else 0)
