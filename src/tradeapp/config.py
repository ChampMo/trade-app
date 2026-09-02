"""Runtime settings.

Values come from environment variables or a `.env` file on the machine that runs the core.
The UI never reads this file and never stores secrets (CLAUDE.md rule 8).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # --- MT5 / broker ---
    mt5_path: str | None = Field(default=None, description="Path to terminal64.exe of the terminal to attach to")
    mt5_login: int | None = None
    mt5_password: SecretStr | None = None
    mt5_server: str | None = None
    mt5_timeout_ms: int = 60_000

    # Hard guard (rule 8). Only the live host ever sets this, by hand, in Phase 5.
    allow_live: bool = Field(default=False, description="Must be true to connect to a REAL account")

    # --- journal ---
    journal_db: Path = Path("data/journal.db")

    # --- identity ---
    magic_base: int = 100_000

    # --- run-time AI (Phase 3) ---
    deepseek_api_key: SecretStr | None = None
    deepseek_daily_budget_usd: float = 2.0

    # --- alerts (Phase 4) ---
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    @property
    def mt5_password_plain(self) -> str | None:
        return self.mt5_password.get_secret_value() if self.mt5_password else None


def load_settings(**overrides) -> Settings:
    """Build Settings from the environment, with explicit overrides (used by tests and the CLI)."""
    return Settings(**overrides)
