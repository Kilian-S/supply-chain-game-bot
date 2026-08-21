"""Discord webhook transport for remote monitoring of a running bot.

The bot was deployed unattended on an EC2 instance for the duration of each
game, so the only practical way to observe it was to have it report to a Discord
channel. This module carries the transport and the notifications that are common
to both runs. Each run supplies its own per-cycle decision summary, because the
Single-Region Run reports one warehouse and the Network Run reports four across
three independent systems.

The webhook URL is read from the DISCORD_WEBHOOK_URL environment variable. If it
is unset, every function here becomes a no-op and the bot runs unmonitored. No
webhook URL is ever written into this file.
"""

import os
import logging
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 5
DISCORD_CONTENT_LIMIT = 2000
DISCORD_FIELD_LIMIT = 1024

# Embed colours, chosen so that a glance at the channel conveys bot state.
COLOUR_INFO = 0x3498DB
COLOUR_SUCCESS = 0x2ECC71
COLOUR_WARNING = 0xF39C12
COLOUR_ERROR = 0xE74C3C
COLOUR_NEUTRAL = 0x95A5A6


def webhook_url(override: str = None) -> str:
    """Return the webhook URL to post to, or an empty string if unconfigured."""
    return override or os.environ.get("DISCORD_WEBHOOK_URL", "")


def send_message(content: str, url: str = None):
    """Post a plain text message. Failures are swallowed by design."""
    target = webhook_url(url)
    if not target:
        return

    try:
        requests.post(
            target,
            json={"content": content[:DISCORD_CONTENT_LIMIT]},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except Exception:
        # A monitoring outage must never take down the bot it is monitoring.
        pass


def send_embed(title: str, fields: dict = None, description: str = None,
               colour: int = COLOUR_INFO, url: str = None):
    """Post a rich embed. Failures are swallowed by design."""
    target = webhook_url(url)
    if not target:
        return

    embed = {
        "title": title,
        "color": colour,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if description is not None:
        embed["description"] = description[:4096]
    if fields:
        embed["fields"] = [
            {"name": name, "value": str(value), "inline": True}
            for name, value in fields.items()
        ]

    try:
        requests.post(
            target, json={"embeds": [embed]}, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except Exception:
        pass


def notify_startup(current_day: int):
    """Report that the bot has authenticated and entered its main loop."""
    send_embed(
        title="Bot started",
        fields={"Day": current_day, "Status": "Logged in and running"},
        colour=COLOUR_SUCCESS,
    )


def notify_error(message: str, day: int = None):
    """Report an exception raised inside the main loop."""
    fields = {"Error": message[:DISCORD_FIELD_LIMIT]}
    if day is not None:
        fields["Day"] = day
    send_embed(title="Bot error", fields=fields, colour=COLOUR_ERROR)


def notify_shutdown(reason: str, day: int = None):
    """Report that the main loop has exited."""
    fields = {"Reason": reason}
    if day is not None:
        fields["Day"] = day
    send_embed(title="Bot stopped", fields=fields, colour=COLOUR_NEUTRAL)


def notify_recovery(current_day: int, attempt: int):
    """Report that a dead browser session was replaced and the loop resumed."""
    send_embed(
        title="Bot recovered",
        fields={
            "Day": current_day,
            "Recovery attempt": attempt,
            "Status": "New browser session created",
        },
        colour=COLOUR_WARNING,
    )


class DiscordHandler(logging.Handler):
    """Logging handler that forwards WARNING and above to Discord.

    Attaching this to the root logger means that every existing warning and
    error call site becomes a remote alert without any change to those call
    sites.
    """

    def __init__(self, level=logging.WARNING, url: str = None):
        super().__init__(level)
        self.url = url

    def emit(self, record):
        try:
            send_message(f"**[{record.levelname}]** {self.format(record)}", url=self.url)
        except Exception:
            pass
