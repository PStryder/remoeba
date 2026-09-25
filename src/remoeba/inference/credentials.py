"""The provider credential: where it comes from, and where it must not go (R1).

The key is read from the environment variable `[inference].api_key_env`
names, by the inference service process only. Everything the supervisor
spawns other than that service -- Ego, Id, neuocytes, sandboxes, the MCP
facade -- is started from :func:`child_environment`, which removes it. A mind
cannot leak a credential its process never had.
"""

from __future__ import annotations

import os
from typing import Mapping

from ..config import InferenceConfig

REDACTED = "[credential]"


def load_api_key(inference: InferenceConfig,
                 environ: Mapping[str, str] | None = None) -> str | None:
    """The key for a real provider, or None for the fake. Absent is a refusal."""
    if inference.provider == "fake":
        return None
    env = os.environ if environ is None else environ
    value = (env.get(inference.api_key_env) or "").strip()
    if not value:
        raise ValueError(
            f"[inference].provider is {inference.provider!r} but the environment "
            f"variable {inference.api_key_env} is not set. The key is read from "
            "the environment, never from a config file.")
    return value


def child_environment(inference: InferenceConfig,
                      environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """An environment for any process that is not the inference service.

    Removes the configured variable, and any other variable holding the same
    value -- a key copied under a second name is still the key.
    """
    env = dict(os.environ if environ is None else environ)
    secret = (env.get(inference.api_key_env) or "").strip()
    env.pop(inference.api_key_env, None)
    if secret:
        for name in [n for n, v in env.items() if v.strip() == secret]:
            env.pop(name)
    return env


def redact(value, secret: str | None):
    """Replace the credential wherever it appears in a JSON-shaped value."""
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, REDACTED)
    if isinstance(value, dict):
        return {k: redact(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secret) for v in value]
    return value
