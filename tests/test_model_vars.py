"""Model variables are applied as stated, or refused -- never quietly altered."""

from __future__ import annotations

import pytest

from remoeba.promptlib.model import (MAX_STOP_SEQUENCES, NamespaceError,
                                     validate_model_vars)


def test_too_many_stop_sequences_are_refused_not_truncated():
    """Truncating kept the first eight and dropped the rest without a word, so a
    profile claimed to stop on sequences it no longer stopped on."""
    stops = [f"STOP{i}" for i in range(MAX_STOP_SEQUENCES + 1)]
    with pytest.raises(NamespaceError, match="refused rather than shortened"):
        validate_model_vars({"stop_sequences": stops})


def test_stop_sequences_up_to_the_limit_are_kept_whole():
    stops = [f"STOP{i}" for i in range(MAX_STOP_SEQUENCES)]
    assert validate_model_vars({"stop_sequences": stops}) == {"stop_sequences": stops}
