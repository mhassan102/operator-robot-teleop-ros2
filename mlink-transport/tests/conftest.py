"""Shared fixtures: fake clock, fake network."""

from __future__ import annotations

import pytest

from proto.clock import FakeClock
from proto.sockets import FakeNetwork


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def net() -> FakeNetwork:
    return FakeNetwork()
