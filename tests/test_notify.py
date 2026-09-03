"""Telegram. The outbound half must never break anything; the inbound half must never trust anyone."""

from datetime import UTC, datetime, timedelta

import httpx
import respx

from tradeapp.journal import Journal
from tradeapp.notify import TelegramBridge, TelegramNotifier

API = "https://tg.test"
TOKEN = "111:AAA"
CHAT = "424242"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

SEND = f"{API}/bot{TOKEN}/sendMessage"
UPDATES = f"{API}/bot{TOKEN}/getUpdates"


def notifier(journal: Journal, token=TOKEN, chat=CHAT, **kw) -> TelegramNotifier:
    return TelegramNotifier(token, chat, journal, api=API, now=lambda: NOW, **kw)


def update(text: str, chat_id: str = CHAT, update_id: int = 1) -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": int(chat_id)}, "text": text}}


def status(**over) -> dict:
    base = {
        "state": "RUNNING",
        "equity": 10_000.0,
        "day_start_equity": 10_000.0,
        "peak_equity": 10_500.0,
        "open_positions": 1,
        "frozen": False,
        "service": {"ticks": 42, "last_tick_utc": "2026-09-03T12:00:00+00:00", "last_error": None},
    }
    base.update(over)
    return base


# --- outbound: never breaks anything ----------------------------------------------------


def test_a_message_goes_out(journal: Journal):
    with respx.mock:
        route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert notifier(journal).send("hello") is True
    assert route.calls[0].request.url == SEND
    import json

    body = json.loads(route.calls[0].request.content)
    assert body["chat_id"] == CHAT and body["text"] == "hello"


def test_a_dead_telegram_never_raises(journal: Journal):
    """The kill switch calls this. It must not be able to stop a close from completing."""
    with respx.mock:
        respx.post(SEND).mock(side_effect=httpx.ConnectError("no route"))
        assert notifier(journal).send("hello") is False
    assert any("could not send" in e.message for e in journal.events_where(source="telegram"))


def test_an_http_error_is_swallowed_too(journal: Journal):
    with respx.mock:
        respx.post(SEND).mock(return_value=httpx.Response(401))
        assert notifier(journal).send("x") is False


def test_without_credentials_it_is_simply_off(journal: Journal):
    n = notifier(journal, token=None, chat=None)
    assert n.configured is False
    assert n.send("x") is False
    assert n.poll() == []


def test_critical_goes_out_immediately(journal: Journal):
    with respx.mock:
        route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        notifier(journal).critical("KILLED [daily_loss] down 3.1%", {"trigger": "daily_loss"})
    assert "KILLED" in route.calls[0].request.content.decode()


def test_warnings_are_batched_rather_than_sent_one_by_one(journal: Journal):
    """Forty reconnect warnings in an hour should arrive as one message, not forty."""
    n = notifier(journal)
    for i in range(40):
        n.warn(f"reconnected {i}")

    with respx.mock:
        route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert n.flush_warnings() is True
    body = route.calls[0].request.content.decode()
    assert "40 warning" in body and "and 20 more" in body
    assert len(route.calls) == 1
    assert n.batch.pending == []


def test_nothing_to_flush_sends_nothing(journal: Journal):
    assert notifier(journal).flush_warnings() is False


def test_warnings_do_not_flush_again_until_the_interval_passes(journal: Journal):
    clock = [NOW]
    n = TelegramNotifier(TOKEN, CHAT, journal, api=API, now=lambda: clock[0])
    n.warn("first")
    with respx.mock:
        respx.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert n.flush_warnings() is True
        n.warn("second")
        assert n.flush_warnings() is False  # too soon
        clock[0] = NOW + timedelta(minutes=16)
        assert n.flush_warnings() is True


# --- the heartbeat: silence is the alarm --------------------------------------------------


def test_the_heartbeat_reports_the_state(journal: Journal):
    with respx.mock:
        route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert notifier(journal).heartbeat(status()) is True
    body = route.calls[0].request.content.decode()
    assert "RUNNING" in body and "10,000.00" in body and "ticks 42" in body


def test_the_heartbeat_waits_for_its_interval(journal: Journal):
    clock = [NOW]
    n = TelegramNotifier(TOKEN, CHAT, journal, api=API, now=lambda: clock[0])
    with respx.mock:
        respx.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert n.heartbeat(status()) is True
        assert n.heartbeat(status()) is False
        clock[0] = NOW + timedelta(minutes=16)
        assert n.heartbeat(status()) is True


def test_a_stopped_loop_is_shouted_about_in_the_heartbeat(journal: Journal):
    text = TelegramNotifier.format_status(
        status(service={"ticks": 5, "last_tick_utc": None, "last_error": "ZeroDivisionError: x"})
    )
    assert "LOOP STOPPED" in text


def test_a_freeze_shows_in_the_heartbeat():
    text = TelegramNotifier.format_status(status(frozen=True, freeze_reason="orphan position 500001"))
    assert "FROZEN" in text and "500001" in text


# --- inbound: the dangerous half ------------------------------------------------------------


def test_a_command_from_the_owner_is_accepted(journal: Journal):
    with respx.mock:
        respx.get(UPDATES).mock(return_value=httpx.Response(200, json={"result": [update("/status")]}))
        commands = notifier(journal).poll()
    assert len(commands) == 1 and commands[0].name == "status"


def test_a_message_from_anyone_else_is_dropped(journal: Journal):
    """Anyone who learns the bot's name can message it. This is the whole authentication story."""
    with respx.mock:
        respx.get(UPDATES).mock(return_value=httpx.Response(200, json={"result": [update("/kill", chat_id="999999")]}))
        commands = notifier(journal).poll()

    assert commands == []
    warned = [e for e in journal.events_where(severity="WARN", source="telegram")]
    assert warned and "unknown chat" in warned[0].message
    assert warned[0].data["chat_id"] == "999999"


def test_updates_are_not_replayed(journal: Journal):
    n = notifier(journal)
    with respx.mock:
        route = respx.get(UPDATES).mock(
            return_value=httpx.Response(200, json={"result": [update("/status", update_id=7)]})
        )
        n.poll()
        n.poll()
    assert "offset=8" in str(route.calls[1].request.url)


def test_a_failing_poll_is_journaled_not_raised(journal: Journal):
    with respx.mock:
        respx.get(UPDATES).mock(side_effect=httpx.ConnectError("down"))
        assert notifier(journal).poll() == []
    assert any("could not read" in e.message for e in journal.events_where(source="telegram"))


def test_command_names_survive_bot_suffixes_and_case(journal: Journal):
    from tradeapp.notify import Command

    assert Command("/Kill@my_bot now", CHAT, 1).name == "kill"
    assert Command("  /status  ", CHAT, 1).name == "status"
    assert Command("", CHAT, 1).name == ""


# --- the bridge: commands reach the core -------------------------------------------------------


class FakeService:
    def __init__(self):
        self.killed_with = None

    def status(self):
        return status()

    def kill(self, reason):
        self.killed_with = reason
        return {"summary": "KILLED [manual] closed 1 position(s), none left open"}


def bridge(journal: Journal):
    service = FakeService()
    return TelegramBridge(notifier(journal), service), service


def test_status_from_the_phone(journal: Journal):
    b, _ = bridge(journal)
    with respx.mock:
        route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert b.handle(_cmd("/status")) == "status"
    assert "RUNNING" in route.calls[0].request.content.decode()


def test_kill_from_the_phone_actually_kills(journal: Journal):
    """The reason this exists: the owner is out and the account needs flattening now."""
    b, service = bridge(journal)
    with respx.mock:
        route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert b.handle(_cmd("/kill")) == "kill"
    assert service.killed_with == "kill from Telegram"
    assert "none left open" in route.calls[0].request.content.decode()
    assert any("Telegram command: /kill" in e.message for e in journal.events_where(severity="WARN"))


def test_unlock_is_deliberately_not_a_telegram_command(journal: Journal):
    """Coming back from an emergency needs the journal, which a phone in a pocket is not."""
    b, _ = bridge(journal)
    with respx.mock:
        route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert b.handle(_cmd("/unlock")) == "unknown"
    assert "I only understand" in route.calls[0].request.content.decode()


def test_help_lists_what_there_is(journal: Journal):
    b, _ = bridge(journal)
    with respx.mock:
        route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        b.handle(_cmd("/help"))
    body = route.calls[0].request.content.decode()
    assert "/status" in body and "/kill" in body


def test_a_tick_answers_commands_and_sends_the_heartbeat(journal: Journal):
    b, service = bridge(journal)
    with respx.mock:
        respx.get(UPDATES).mock(return_value=httpx.Response(200, json={"result": [update("/status")]}))
        send = respx.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert b.tick() == ["status"]
    assert len(send.calls) == 2  # the answer, then the heartbeat


def _cmd(text: str):
    from tradeapp.notify import Command

    return Command(text=text, chat_id=CHAT, update_id=1)
