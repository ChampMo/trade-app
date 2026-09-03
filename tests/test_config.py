"""Settings must survive the shipped .env template, and must keep demo and live apart (D8)."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from tradeapp.config import Profile, Settings

NL = chr(10)


def _settings_from(tmp_path: Path, body: str) -> Settings:
    env = tmp_path / ".env"
    env.write_text(body, encoding="utf-8")
    return Settings(_env_file=env)


def test_blank_values_are_treated_as_unset(tmp_path: Path):
    s = _settings_from(
        tmp_path,
        "MT5_PATH=C:\\MT5\\terminal64.exe\nMT5_LOGIN=\nMT5_PASSWORD=\nMT5_SERVER=\nDEEPSEEK_API_KEY=\n",
    )
    assert s.mt5_login is None
    assert s.mt5_password is None and s.mt5_password_plain is None
    assert s.mt5_server is None
    assert s.deepseek_api_key is None
    assert s.mt5_path == "C:\\MT5\\terminal64.exe"


def test_shipped_example_template_loads():
    example = Path(__file__).resolve().parents[1] / ".env.example"
    s = Settings(_env_file=example)
    assert s.mt5_login is None and s.allow_live is False


def test_filled_values_are_parsed(tmp_path: Path):
    s = _settings_from(tmp_path, "MT5_LOGIN=318639355\nMT5_PASSWORD=hunter2\nMT5_SERVER=XMGlobal-Demo 3\n")
    assert s.mt5_login == 318639355
    assert isinstance(s.mt5_password, SecretStr) and s.mt5_password_plain == "hunter2"
    assert s.mt5_server == "XMGlobal-Demo 3"  # spaces preserved


def test_allow_live_defaults_false(tmp_path: Path):
    assert _settings_from(tmp_path, "").allow_live is False
    assert _settings_from(tmp_path, "ALLOW_LIVE=false\n").allow_live is False


# --- profiles (D8, P1-01) ---


def test_default_profile_is_demo(tmp_path: Path):
    s = _settings_from(tmp_path, "")
    assert s.profile is Profile.DEMO and s.live_enabled is False


def test_each_profile_gets_its_own_journal_and_port(tmp_path: Path):
    demo = _settings_from(tmp_path, "PROFILE=demo\n")
    live = _settings_from(tmp_path, "PROFILE=live\n")
    paper = _settings_from(tmp_path, "PROFILE=paper\n")

    assert demo.journal_path.name == "journal-demo.db"
    assert live.journal_path.name == "journal-live.db"
    assert demo.journal_path != live.journal_path  # a demo fill must never land in the live history
    assert (demo.port, live.port) == (8001, 8002)
    assert paper.port == 8001  # paper is a mode of the demo core, not a third process


def test_explicit_journal_db_wins_over_the_profile_default(tmp_path: Path):
    s = _settings_from(tmp_path, "PROFILE=live" + NL + "JOURNAL_DB=data/custom.db")
    assert s.journal_path.name == "custom.db"
    assert s.journal_path.parent.name == "data"


def test_simulated_journal_is_a_separate_file(tmp_path: Path):
    s = _settings_from(tmp_path, "JOURNAL_DB=data/journal.db")
    assert s.journal_path.name == "journal.db"
    assert s.simulated_journal_path.name == "journal-fake.db"
    assert s.simulated_journal_path.parent == s.journal_path.parent


def test_api_port_can_be_overridden(tmp_path: Path):
    assert _settings_from(tmp_path, "PROFILE=demo\nAPI_PORT=9100\n").port == 9100


def test_allow_live_on_a_non_live_profile_is_refused(tmp_path: Path):
    """Rule 8: the dangerous combination must not be loadable at all."""
    for profile in ("demo", "paper"):
        with pytest.raises(ValidationError, match="only honoured on the live profile"):
            _settings_from(tmp_path, f"PROFILE={profile}\nALLOW_LIVE=true\n")


def test_live_enabled_needs_both_the_profile_and_the_flag(tmp_path: Path):
    assert _settings_from(tmp_path, "PROFILE=live\nALLOW_LIVE=true\n").live_enabled is True
    # a live host that has not finished setup loads fine, but still cannot trade
    assert _settings_from(tmp_path, "PROFILE=live\n").live_enabled is False
    assert _settings_from(tmp_path, "PROFILE=demo\n").live_enabled is False


# --- paths must not follow the shell (found live, 2026-09-03) --------------------------------
#
# The core was started from `ui/` by someone who was already there for `npm start`. It came up
# looking perfectly healthy while writing its journal to ui/data/journal-demo.db, reading no
# .env at all, and finding no stored bars — a backtest launched from the UI failed with
# "only 0 bars stored". The quiet half is the dangerous one: peak equity lives in the journal so
# that a restart cannot erase the drawdown history (D21), and a journal that follows the cwd
# resets that history every time the core is started from somewhere new.


def test_the_journal_does_not_follow_the_current_directory(tmp_path: Path, monkeypatch):
    from tradeapp.config import PROJECT_ROOT

    before = Settings().journal_path
    monkeypatch.chdir(tmp_path)
    after = Settings().journal_path

    assert before == after
    assert after.is_absolute()
    assert after.is_relative_to(PROJECT_ROOT)


def test_stored_bars_and_the_calendar_are_found_from_anywhere(tmp_path: Path, monkeypatch):
    before = (Settings().history_path, Settings().calendar_path)
    monkeypatch.chdir(tmp_path)
    after = (Settings().history_path, Settings().calendar_path)

    assert before == after
    assert all(p.is_absolute() for p in after)


def test_an_absolute_path_is_left_exactly_as_given(tmp_path: Path):
    wanted = tmp_path / "elsewhere" / "journal.db"
    s = _settings_from(tmp_path, f"JOURNAL_DB={wanted}")
    assert s.journal_path == wanted


def test_the_anchor_can_be_moved_deliberately(tmp_path: Path):
    """A VPS may keep its data on another drive; that is a setting, not an accident."""
    s = _settings_from(tmp_path, f"DATA_DIR={tmp_path}" + NL + "JOURNAL_DB=data/journal.db")
    assert s.journal_path == tmp_path / "data" / "journal.db"
