"""Settings must survive the shipped .env template, which leaves optional keys empty on purpose."""

from pathlib import Path

from pydantic import SecretStr

from tradeapp.config import Settings


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


def test_shipped_example_template_loads(tmp_path: Path):
    example = Path(__file__).resolve().parents[1] / ".env.example"
    s = Settings(_env_file=example)
    assert s.mt5_login is None and s.allow_live is False


def test_filled_values_are_parsed(tmp_path: Path):
    s = _settings_from(tmp_path, "MT5_LOGIN=318639355\nMT5_PASSWORD=hunter2\nMT5_SERVER=XMGlobal-Demo 3\n")
    assert s.mt5_login == 318639355
    assert isinstance(s.mt5_password, SecretStr) and s.mt5_password_plain == "hunter2"
    assert s.mt5_server == "XMGlobal-Demo 3"  # spaces preserved


def test_allow_live_defaults_false_and_needs_explicit_true(tmp_path: Path):
    assert _settings_from(tmp_path, "").allow_live is False
    assert _settings_from(tmp_path, "ALLOW_LIVE=false\n").allow_live is False
    assert _settings_from(tmp_path, "ALLOW_LIVE=true\n").allow_live is True


def test_fake_journal_is_a_separate_file():
    from tradeapp.__main__ import fake_journal_path

    assert fake_journal_path("data/journal.db").as_posix() == "data/journal-fake.db"
    assert fake_journal_path(Path("C:/x/journal.db")).name == "journal-fake.db"
