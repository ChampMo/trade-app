from __future__ import annotations

import pytest

from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.journal import Journal


@pytest.fixture
def journal() -> Journal:
    j = Journal(":memory:")
    yield j
    j.close()


@pytest.fixture
def fake_broker() -> FakeBroker:
    return FakeBroker(behavior=FakeBehavior())
