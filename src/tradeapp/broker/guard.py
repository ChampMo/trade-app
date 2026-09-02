"""Live-account guard (rule 8). One function, used by every Broker implementation on connect."""

from __future__ import annotations

from tradeapp.contracts import AccountInfo, AccountMode, LiveAccountBlocked


def enforce_live_guard(account: AccountInfo, allow_live: bool) -> None:
    """Raise LiveAccountBlocked when the account is REAL and ALLOW_LIVE is not set.

    Demo and contest accounts always pass. The caller must disconnect before re-raising
    so a blocked terminal session is never left open.
    """
    if account.mode is AccountMode.REAL and not allow_live:
        raise LiveAccountBlocked(
            f"account {account.login}@{account.server} is REAL and ALLOW_LIVE is not set; refusing to connect"
        )
