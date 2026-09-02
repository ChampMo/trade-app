"""Runtime settings.

Values come from environment variables or a `.env` file on the machine that runs the core.
The UI never reads this file and never stores secrets (CLAUDE.md rule 8).

A **profile** (D8) is one core process + one MT5 terminal + one execution mode. Demo and live are
separate installs, separate ports and separate journals, so nothing about development can reach a
real account by accident. `ALLOW_LIVE` is honoured only on the live profile, and setting it anywhere
else is a hard configuration error rather than a warning nobody reads.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Profile(StrEnum):
    DEMO = "demo"  # broker demo account, real fills from the demo server
    PAPER = "paper"  # demo feed, fills simulated in-process (P1-07)
    LIVE = "live"  # real money


DEFAULT_PORTS: dict[Profile, int] = {
    Profile.DEMO: 8001,
    Profile.PAPER: 8001,  # paper is a mode of the demo core, not a second process
    Profile.LIVE: 8002,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, data):
        """`KEY=` in a .env file arrives as "". Treat it as absent, not as a value to parse.

        Without this, the shipped template (which leaves MT5_LOGIN empty on purpose) fails
        validation before the bridge ever runs.
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and v.strip() == "")}
        return data

    # --- profile (D8) ---
    profile: Profile = Profile.DEMO
    api_port: int | None = Field(default=None, description="Overrides the profile's default port")

    # --- MT5 / broker ---
    mt5_path: str | None = Field(default=None, description="Path to terminal64.exe of the terminal to attach to")
    mt5_login: int | None = None
    mt5_password: SecretStr | None = None
    mt5_server: str | None = None
    mt5_timeout_ms: int = 60_000
    reference_symbol: str = "EURUSD"

    # Hard guard (rule 8). Only the live host ever sets this, by hand, in Phase 5.
    allow_live: bool = Field(default=False, description="Must be true to connect to a REAL account")

    # --- journal ---
    journal_db: Path | None = Field(default=None, description="Defaults to data/journal-<profile>.db")

    # --- identity ---
    magic_base: int = 100_000

    # --- run-time AI (Phase 3) ---
    deepseek_api_key: SecretStr | None = None
    deepseek_daily_budget_usd: float = 2.0

    # --- alerts (Phase 4) ---
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    @model_validator(mode="after")
    def _live_flag_belongs_to_the_live_profile(self) -> Settings:
        """Rule 8. A demo .env carrying ALLOW_LIVE is a mistake, not a preference.

        Refusing here means the dangerous combination cannot exist at all. The reverse pairing
        (live profile, flag unset) is allowed to load: the broker guard then refuses the account,
        which is the correct outcome for someone who has not finished setting the host up.
        """
        if self.allow_live and self.profile is not Profile.LIVE:
            raise ValueError(
                f"ALLOW_LIVE is set on the '{self.profile.value}' profile. It is only honoured on the live "
                "profile; remove it from this .env, or set PROFILE=live if this really is the live host."
            )
        return self

    @property
    def live_enabled(self) -> bool:
        """The single value the broker guard should be handed."""
        return self.allow_live and self.profile is Profile.LIVE

    @property
    def port(self) -> int:
        return self.api_port or DEFAULT_PORTS[self.profile]

    @property
    def journal_path(self) -> Path:
        """Each profile keeps its own record; a demo fill must never sit in the live history."""
        return self.journal_db or Path(f"data/journal-{self.profile.value}.db")

    @property
    def simulated_journal_path(self) -> Path:
        """Sibling file for runs whose fills are invented (`smoke --fake`, paper experiments)."""
        p = self.journal_path
        return p.with_name(f"{p.stem}-fake{p.suffix}")

    @property
    def mt5_password_plain(self) -> str | None:
        return self.mt5_password.get_secret_value() if self.mt5_password else None

    def describe(self) -> str:
        return (
            f"profile={self.profile.value} port={self.port} journal={self.journal_path} "
            f"live_enabled={self.live_enabled}"
        )


def load_settings(**overrides) -> Settings:
    """Build Settings from the environment, with explicit overrides (used by tests and the CLI)."""
    return Settings(**overrides)
