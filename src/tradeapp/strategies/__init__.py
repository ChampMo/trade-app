"""Strategy plugins.

Adding a strategy is adding one file here and decorating the class with `@register`. Nothing in
the engine changes, which is the property that makes it safe to let an unattended session write
one: the blast radius of a new strategy is a single file, and the Risk Engine still stands between
it and the market.

A strategy knows about bars and indicators. It does not know the account balance, how many
positions are open, or what the other strategies are doing, and it has no way to place an order.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    """Class decorator. Validates the contract early, where the error is readable."""
    for attr in ("id", "symbols", "timeframe"):
        if not hasattr(cls, attr):
            raise TypeError(f"{cls.__name__} is missing required attribute '{attr}'")
    if not callable(getattr(cls, "on_bar", None)):
        raise TypeError(f"{cls.__name__} must define on_bar(self, ctx)")
    sid = cls.id
    if not isinstance(sid, str) or not sid:
        raise TypeError(f"{cls.__name__}.id must be a non-empty string")
    if sid in REGISTRY and REGISTRY[sid] is not cls:
        raise ValueError(f"strategy id {sid!r} is already registered by {REGISTRY[sid].__name__}")
    REGISTRY[sid] = cls
    return cls


def discover(reload: bool = False) -> dict[str, type]:
    """Import every module in this package so decorators run, then return the registry."""
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        name = f"{__name__}.{mod.name}"
        module = importlib.import_module(name)
        if reload:
            importlib.reload(module)
    return dict(REGISTRY)


def create(strategy_id: str, **params: Any):
    """Build one strategy by id, with parameter overrides."""
    discover()
    if strategy_id not in REGISTRY:
        raise KeyError(f"unknown strategy {strategy_id!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[strategy_id](**params)
