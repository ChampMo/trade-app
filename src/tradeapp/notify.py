"""Telegram: the alert channel, and the only way to reach the kill switch from a phone (P4-02).

Two directions, and they carry very different risk.

Outbound is easy: a CRIT goes out immediately, WARNs are batched so an hour of reconnect noise is
one message rather than forty, and a heartbeat every fifteen minutes means silence itself is the
alarm. Nothing here may ever raise into the caller — a dead notifier must not be able to stop a
kill from completing.

Inbound is the dangerous half. Anyone who learns the bot's name can message it, so **every update
is checked against the configured chat id** and anything else is dropped and journaled. That check
is the entire authentication story, which is why it is one line with a test on it rather than
something clever.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from tradeapp.journal import Journal

SOURCE = "telegram"
API = "https://api.telegram.org"


@dataclass(frozen=True)
class Command:
    text: str
    chat_id: str
    update_id: int

    @property
    def name(self) -> str:
        return self.text.strip().split()[0].lstrip("/").split("@")[0].lower() if self.text.strip() else ""


@dataclass
class Batch:
    """WARNs collect here so a noisy hour arrives as one message."""

    every: timedelta = timedelta(minutes=15)
    pending: list[str] = field(default_factory=list)
    last_sent: datetime | None = None

    def due(self, now: datetime) -> bool:
        return bool(self.pending) and (self.last_sent is None or now - self.last_sent >= self.every)


class TelegramNotifier:
    def __init__(
        self,
        token: str | None,
        chat_id: str | None,
        journal: Journal,
        *,
        api: str = API,
        timeout_s: float = 15.0,
        heartbeat_every: timedelta = timedelta(minutes=15),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.token = token
        self.chat_id = str(chat_id) if chat_id else None
        self.journal = journal
        self.api = api
        self.timeout_s = timeout_s
        self.heartbeat_every = heartbeat_every
        self._now = now
        self.batch = Batch()
        self.last_heartbeat: datetime | None = None
        self._offset = 0

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    # --- outbound -------------------------------------------------------------------

    def send(self, text: str) -> bool:
        """Never raises. A broken notifier is a degraded system, not a stopped one."""
        if not self.configured:
            return False
        import httpx

        try:
            response = httpx.post(
                f"{self.api}/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True},
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            return True
        except Exception as e:  # noqa: BLE001
            self.journal.event("WARN", SOURCE, "could not send a Telegram message", {"error": str(e)})
            return False

    def critical(self, message: str, data: dict | None = None) -> None:
        """The `Notifier` protocol the kill switch expects. Goes out immediately."""
        self.send(f"🔴 {message}" + (f"\n{data}" if data else ""))

    def warn(self, message: str) -> None:
        self.batch.pending.append(message)

    def flush_warnings(self) -> bool:
        now = self._now()
        if not self.batch.due(now):
            return False
        lines = self.batch.pending[:20]
        extra = len(self.batch.pending) - len(lines)
        body = "\n".join(f"• {line}" for line in lines) + (f"\n… and {extra} more" if extra > 0 else "")
        sent = self.send(f"⚠️ {len(self.batch.pending)} warning(s)\n{body}")
        self.batch.pending.clear()
        self.batch.last_sent = now
        return sent

    def heartbeat(self, status: dict, force: bool = False) -> bool:
        """Silence is the alarm, so this is the message that matters most when nothing is wrong."""
        now = self._now()
        if not force and self.last_heartbeat is not None and now - self.last_heartbeat < self.heartbeat_every:
            return False
        self.last_heartbeat = now
        self.send(self.format_status(status))
        return True

    @staticmethod
    def format_status(status: dict) -> str:
        service = status.get("service") or {}
        state = status.get("state", "?")
        icon = {"RUNNING": "🟢", "PAUSED": "🟡", "KILLED": "🔴"}.get(state, "⚪")
        lines = [
            f"{icon} {state}",
            f"equity {status.get('equity', 0):,.2f}   open {status.get('open_positions', 0)}",
            f"day start {status.get('day_start_equity', 0):,.2f}   peak {status.get('peak_equity', 0):,.2f}",
            f"ticks {service.get('ticks', 0)}   last {(service.get('last_tick_utc') or '?')[11:19]} UTC",
        ]
        if status.get("frozen"):
            lines.append(f"FROZEN: {status.get('freeze_reason')}")
        if service.get("last_error"):
            lines.append(f"LOOP STOPPED: {service['last_error']}")
        return "\n".join(lines)

    # --- inbound: the dangerous half ---------------------------------------------------

    def poll(self) -> list[Command]:
        """Fetch new commands. Anything from another chat is dropped and journaled."""
        if not self.configured:
            return []
        import httpx

        try:
            response = httpx.get(
                f"{self.api}/bot{self.token}/getUpdates",
                params={"offset": self._offset + 1, "timeout": 0},
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as e:  # noqa: BLE001
            self.journal.event("WARN", SOURCE, "could not read Telegram updates", {"error": str(e)})
            return []

        out: list[Command] = []
        for update in payload.get("result", []):
            self._offset = max(self._offset, int(update.get("update_id", 0)))
            message = update.get("message") or update.get("edited_message") or {}
            chat_id = str((message.get("chat") or {}).get("id", ""))
            text = message.get("text", "")
            if not text:
                continue
            # The whole authentication story, on purpose: one comparison, easy to audit.
            if chat_id != self.chat_id:
                self.journal.event(
                    "WARN",
                    SOURCE,
                    "ignored a Telegram message from an unknown chat",
                    {"chat_id": chat_id, "text": text[:80]},
                )
                continue
            out.append(Command(text=text, chat_id=chat_id, update_id=int(update.get("update_id", 0))))
        return out


HELP = "/status — how things are\n/kill — close everything and stop\n/help — this"


class TelegramBridge:
    """Runs the notifier against a `CoreService`: heartbeats out, commands in.

    `/kill` works from a phone because that is the point. `/unlock` deliberately does not: coming
    back from an emergency should require looking at the journal, which a phone in a pocket is not.
    """

    def __init__(
        self,
        notifier: TelegramNotifier,
        service,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.notifier = notifier
        self.service = service
        self._now = now

    def tick(self) -> list[str]:
        """One pass: answer commands, send the heartbeat if due, flush batched warnings."""
        handled = []
        for command in self.notifier.poll():
            handled.append(self.handle(command))
        self.notifier.heartbeat(self.service.status())
        self.notifier.flush_warnings()
        return handled

    def handle(self, command: Command) -> str:
        name = command.name
        self.journal_event(command)
        if name == "status":
            self.notifier.send(self.notifier.format_status(self.service.status()))
            return "status"
        if name == "kill":
            report = self.service.kill("kill from Telegram")
            self.notifier.send(f"🔴 {report['summary']}")
            return "kill"
        if name in {"help", "start"}:
            self.notifier.send(HELP)
            return "help"
        self.notifier.send(f"I only understand:\n{HELP}")
        return "unknown"

    def journal_event(self, command: Command) -> None:
        self.notifier.journal.event(
            "WARN" if command.name == "kill" else "INFO",
            SOURCE,
            f"Telegram command: /{command.name or '?'}",
            {"text": command.text[:120]},
        )

    def run(self, should_stop: Callable[[], bool] = lambda: False, interval_s: float = 5.0) -> None:
        while not should_stop():
            self.tick()
            time.sleep(interval_s)
