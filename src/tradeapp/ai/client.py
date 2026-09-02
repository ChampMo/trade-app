"""DeepSeek over plain HTTP (P3-02).

No SDK on purpose. It is one POST to one URL, and keeping it that way means swapping provider is
a change to this file rather than to the project's dependency tree.

Three rules this file exists to enforce, all from D6:

- **A daily budget.** Spend is tracked in the journal, so it survives a restart. Past the cap the
  client stops answering and the system trades on rules alone. It does not stop the system.
- **Every call is recorded raw.** Prompt and reply both go into `ai_calls`, which is what makes it
  possible to replay a year of decisions later and ask what the AI layer was actually worth.
- **No account data leaves the machine.** The prompt carries market context and nothing else — no
  balance, no positions, no ticket numbers. There is no reason a model needs them, and every
  reason not to send them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from tradeapp.ai import schemas
from tradeapp.journal import Journal

SOURCE = "ai"
DEFAULT_URL = "https://api.deepseek.com/chat/completions"
SPEND_KEY = "ai_spend"


@dataclass(frozen=True)
class Pricing:
    """USD per million tokens. Providers change these; keep them here and check occasionally."""

    input_per_m: float = 0.27
    output_per_m: float = 1.10

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in / 1_000_000) * self.input_per_m + (tokens_out / 1_000_000) * self.output_per_m


class BudgetExceeded(RuntimeError):
    """Past the daily cap. Not an error condition for the system — just no AI today."""


@dataclass
class Reply:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    model: str
    call_id: int | None = None


class DeepSeekClient:
    def __init__(
        self,
        api_key: str | None,
        journal: Journal,
        *,
        url: str = DEFAULT_URL,
        model: str = "deepseek-chat",
        daily_budget_usd: float = 2.0,
        timeout_s: float = 45.0,
        pricing: Pricing | None = None,
        now=lambda: datetime.now(UTC),
    ) -> None:
        self.api_key = api_key
        self.journal = journal
        self.url = url
        self.model = model
        self.daily_budget_usd = daily_budget_usd
        self.timeout_s = timeout_s
        self.pricing = pricing or Pricing()
        self._now = now

    # --- budget -------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """False means the system runs on rules alone, which must always be a working state."""
        return bool(self.api_key) and self.spent_today < self.daily_budget_usd

    def _day(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    @property
    def spent_today(self) -> float:
        return float(self.journal.get_state(f"{SPEND_KEY}:{self._day()}", 0.0) or 0.0)

    def _record_spend(self, cost: float) -> None:
        self.journal.set_state(f"{SPEND_KEY}:{self._day()}", round(self.spent_today + cost, 6))

    # --- calling ------------------------------------------------------------------

    def ask(self, agent: str, system: str, user: str, *, model: str | None = None) -> Reply:
        """One call. Raises BudgetExceeded or RuntimeError; never returns something unusable."""
        if not self.api_key:
            raise BudgetExceeded("no DeepSeek API key configured; running on rules alone")
        if self.spent_today >= self.daily_budget_usd:
            raise BudgetExceeded(
                f"daily AI budget spent ({self.spent_today:.4f} of {self.daily_budget_usd:.2f} USD); "
                "the system keeps trading on rules"
            )

        import httpx

        use_model = model or self.model
        payload = {
            "model": use_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        prompt_record = f"[system]\n{system}\n\n[user]\n{user}"

        try:
            response = httpx.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as e:  # noqa: BLE001 - a failed call is a normal condition, not a crash
            self.journal.ai_call(
                agent=agent,
                model=use_model,
                prompt=prompt_record,
                response=None,
                schema_ok=False,
                error=f"{type(e).__name__}: {e}",
            )
            raise RuntimeError(f"DeepSeek call failed: {type(e).__name__}: {e}") from e

        text = _content(body)
        usage = body.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens", 0))
        tokens_out = int(usage.get("completion_tokens", 0))
        cost = self.pricing.cost(tokens_in, tokens_out)
        self._record_spend(cost)

        call_id = self.journal.ai_call(
            agent=agent,
            model=use_model,
            prompt=prompt_record,
            response=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=round(cost, 6),
            schema_ok=None,
        )
        return Reply(
            text=text, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost, model=use_model, call_id=call_id
        )

    def ask_json(self, agent: str, system: str, user: str, model_type: type[BaseModel], **kw) -> BaseModel:
        """Ask, then insist the reply fits the schema. A bad shape is a failure, never a retry loop."""
        reply = self.ask(agent, system, user, **kw)
        try:
            parsed = schemas.parse(model_type, reply.text)
        except ValueError as e:
            self.journal.update_ai_call(reply.call_id, schema_ok=False, error=str(e))
            self.journal.event("WARN", SOURCE, f"{agent} reply did not match the schema", {"error": str(e)})
            raise ValueError(str(e)) from e
        self.journal.update_ai_call(reply.call_id, schema_ok=True, parsed=parsed.model_dump())
        return parsed


def _content(body: dict[str, Any]) -> str:
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"unexpected reply shape from the provider: {body}") from e
