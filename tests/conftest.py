"""Fixtures for the foundation carried over from Amoeba.

Amoeba's conftest also started live process stacks against a deterministic
inference backend. Neither the stack nor the backend exists here yet: the
inference contract is being redesigned for remote APIs (docs/PORTING.md), so
only the fixtures the foundation tests use are carried.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from remoeba.config import Config  # noqa: E402
from remoeba.mind import Mind  # noqa: E402


@pytest.fixture()
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.state_dir = tmp_path / "state"
    c.runtime_dir = tmp_path / "runtime"
    c.models_dir = tmp_path / "models"
    c.ensure_dirs()
    return c


@pytest.fixture()
def mind(cfg: Config) -> Iterator[Mind]:
    m = Mind(cfg)
    yield m
    m.close()
