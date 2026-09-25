"""The inference service: the one process that talks to a model provider.

What it is for, in the order it matters:

- **It holds the credential and nothing else does (R1).** Its RPC token is
  given to the supervisor only, so no role or neuocyte can call it: every
  model request is built by the Harness from the record, which is what makes
  R2 and R6 enforceable in one place.
- **It sends only what was committed (R2).** It never writes state -- the
  supervisor is the single writer (I1). The supervisor commits a request body
  and hands the service the body and its digest; the service refuses a body
  whose digest does not match, then sends exactly those bytes.
- **It sends only what the class means.** Every body is checked against the
  class's pin and the pinned endpoint's declared capabilities at send time,
  whoever built it.
- **It reports what happened, and nothing more.** Each attempt, each wait,
  the classified outcome and the raw response go back to the supervisor to be
  recorded. The serving provider is *unconfirmed* until
  :meth:`confirm_served` reads it from the provider's generation record.

    python -m remoeba.inference.service --config config.toml
"""

from __future__ import annotations

import argparse
import base64
import json
import threading
import time
import urllib.parse
from typing import Any, Callable

from ..config import Config, InferenceConfig, load_config
from ..errors import BackendUnavailable, IntegrityError, InvalidInput
from . import wire
from .credentials import load_api_key, redact
from .transport import FakeTransport, HttpTransport, Transport, TransportError

SERVICE_NAME = "inference"


class InferenceService:
    def __init__(self, inference: InferenceConfig, transport: Transport, *,
                 secret: str | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = inference
        self.transport = transport
        self._secret = secret
        self._sleep = sleep
        self._clock = clock
        self.bindings = {
            name: wire.ModelBinding(name, spec.model, spec.endpoint,
                                    spec.data_collection, spec.zdr)
            for name, spec in inference.classes.items()}
        self.caps: dict[str, wire.EndpointCapabilities] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()
        self.started = False
        self.stats = {"sent": 0, "attempts": 0, "refused_before_send": 0}

    # -- startup -------------------------------------------------------------
    def start(self) -> dict[str, Any]:
        """Resolve every class's pinned endpoint, or refuse to start.

        A class whose endpoint is missing, or lacks native tool calling, is a
        configuration error found now rather than in the middle of a turn.
        """
        for name, binding in self.bindings.items():
            listing = self._get(f"/models/{urllib.parse.quote(binding.model, safe='/')}/endpoints")
            caps = wire.endpoint_capabilities(listing, binding.endpoint)
            wire.require_tools(caps)
            self.caps[name] = caps
        self.started = True
        return self.capabilities()

    def _get(self, path: str) -> dict[str, Any]:
        try:
            raw = self.transport.get_json(path, timeout=self.cfg.request_timeout_seconds)
        except TransportError as exc:
            raise BackendUnavailable("the provider could not be reached",
                                     path=path, error=str(exc)) from None
        if raw.status != 200:
            raise BackendUnavailable("the provider refused a metadata request",
                                     path=path, http_status=raw.status,
                                     error=redact(raw.body[:500].decode("utf-8", "replace"),
                                                  self._secret))
        return json.loads(raw.body.decode("utf-8"))

    # -- what the Harness calls ---------------------------------------------
    def binding(self, model_class: str) -> wire.ModelBinding:
        found = self.bindings.get(model_class)
        if found is None:
            # No default model: an unmapped class is refused (decision 2).
            raise InvalidInput("no such model class", model_class=model_class,
                               known=sorted(self.bindings))
        if model_class not in self.caps:
            raise BackendUnavailable("the service has not resolved this class yet",
                                     model_class=model_class)
        return found

    def capabilities(self, model_class: str | None = None) -> dict[str, Any]:
        names = [model_class] if model_class else sorted(self.bindings)
        out = {}
        for name in names:
            binding = self.binding(name)
            out[name] = {"binding": binding.to_dict(),
                         "endpoint": self.caps[name].to_dict()}
        return {"simulated": self.transport.simulated, "classes": out}

    def prepare(self, *, model_class: str, messages: list[dict[str, Any]],
                settings: dict[str, Any],
                tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Build and check a body without sending it.

        The supervisor commits what this returns and then calls :meth:`send`
        with it. Refusals here happen before anything is recorded as sent.
        """
        binding = self.binding(model_class)
        body = wire.build_body(binding, messages, settings=settings, tools=tools)
        wire.check_capabilities(body, self.caps[model_class])
        data = wire.canonical_bytes(body)
        return {"body": data.decode("utf-8"), "sha256": wire.digest(data),
                "binding": binding.to_dict()}

    def send(self, *, model_class: str, body: str, sha256: str,
             call_id: str) -> dict[str, Any]:
        """Send a committed body and report everything that happened."""
        binding = self.binding(model_class)
        data = body.encode("utf-8")
        if wire.digest(data) != sha256:
            self.stats["refused_before_send"] += 1
            raise IntegrityError("the body is not the one whose digest was committed",
                                 expected=sha256, got=wire.digest(data))
        try:
            parsed = json.loads(body)
            wire.check_body(parsed, binding)
            wire.check_capabilities(parsed, self.caps[model_class])
        except Exception:
            self.stats["refused_before_send"] += 1
            raise

        started = self._clock()
        attempts: list[dict[str, Any]] = []
        outcome: dict[str, Any] = {}
        raw_body: bytes | None = None
        self.stats["sent"] += 1
        for n in range(self.cfg.max_retries + 1):
            if self._is_cancelled(call_id):
                outcome = {"kind": wire.Outcome.CANCELLED}
                break
            self.stats["attempts"] += 1
            t0 = self._clock()
            try:
                raw = self.transport.post_chat(
                    data, call_id=call_id, timeout=self.cfg.request_timeout_seconds)
                raw_body = raw.body
                outcome = wire.classify_http(raw.status, raw.body, raw.retry_after)
            except TransportError as exc:
                raw_body = None
                outcome = {"kind": (wire.Outcome.CANCELLED if exc.cancelled
                                    else wire.Outcome.PROVIDER_UNAVAILABLE),
                           "transport_error": str(exc)}
            attempt = {"attempt": n + 1, "kind": outcome["kind"],
                       "http_status": outcome.get("http_status"),
                       "seconds": round(self._clock() - t0, 3)}
            attempts.append(attempt)
            if outcome["kind"] not in wire.Outcome.RETRYABLE or n == self.cfg.max_retries:
                break
            wait = self._wait_for(outcome, n)
            if wait is None:
                attempt["not_retried"] = "the provider asked for a longer wait than allowed"
                break
            attempt["waited_before_next"] = wait
            if not self._wait(wait, call_id):
                outcome = {"kind": wire.Outcome.CANCELLED}
                break

        report: dict[str, Any] = {
            "call_id": call_id,
            "model_class": model_class,
            "binding": binding.to_dict(),
            "request_sha256": sha256,
            "simulated": self.transport.simulated,
            "attempts": attempts,
            "latency_seconds": round(self._clock() - started, 3),
            # Never inferred from the pin: a pin is a request, not evidence.
            "served_by": {"status": "unconfirmed"},
            **outcome,
        }
        if raw_body is not None:
            report["response_sha256"] = wire.digest(raw_body)
            try:
                report["response_body"] = raw_body.decode("utf-8")
            except UnicodeDecodeError:
                report["response_body_b64"] = base64.b64encode(raw_body).decode("ascii")
        with self._lock:
            self._cancelled.discard(call_id)
        return redact(report, self._secret)

    def _wait_for(self, outcome: dict[str, Any], n: int) -> float | None:
        asked = outcome.get("retry_after_seconds")
        if asked is not None:
            return asked if asked <= self.cfg.max_retry_wait_seconds else None
        return min(self.cfg.retry_base_seconds * (2 ** n), self.cfg.max_retry_wait_seconds)

    def _wait(self, seconds: float, call_id: str) -> bool:
        end = self._clock() + seconds
        while self._clock() < end:
            if self._is_cancelled(call_id):
                return False
            self._sleep(min(0.25, max(0.0, end - self._clock())))
        return not self._is_cancelled(call_id)

    def _is_cancelled(self, call_id: str) -> bool:
        with self._lock:
            return call_id in self._cancelled

    def cancel(self, *, call_id: str) -> dict[str, Any]:
        """Stop a call. Tokens already generated may still be billed (I27)."""
        with self._lock:
            self._cancelled.add(call_id)
        in_flight = self.transport.cancel(call_id)
        return {"call_id": call_id, "cancel_requested": True,
                "was_in_flight": bool(in_flight),
                "note": "the provider may still bill tokens generated before the "
                        "connection closed"}

    def confirm_served(self, *, model_class: str, response_id: str) -> dict[str, Any]:
        """Which upstream actually served a response, from the provider's record."""
        caps = self.caps[self.binding(model_class).model_class]
        try:
            raw = self.transport.get_json(
                f"/generation?id={urllib.parse.quote(response_id)}",
                timeout=self.cfg.request_timeout_seconds)
        except TransportError as exc:
            return {"status": "unavailable", "error": str(exc)}
        if raw.status != 200:
            # Generation records can lag the response; unavailable is not absent.
            return {"status": "unavailable", "http_status": raw.status}
        data = (json.loads(raw.body.decode("utf-8")) or {}).get("data") or {}
        served = data.get("provider_name")
        return redact({
            "status": "confirmed",
            "provider_name": served,
            "pinned_provider_name": caps.provider_name,
            "matches_pin": served == caps.provider_name,
            "reported_model": data.get("model"),
            "total_cost": data.get("total_cost"),
            "native_tokens_prompt": data.get("native_tokens_prompt"),
            "native_tokens_completion": data.get("native_tokens_completion"),
            "cancelled": data.get("cancelled"),
        }, self._secret)

    def health(self) -> dict[str, Any]:
        """Answerable whatever the provider is doing (I25): no network here."""
        return {"service": SERVICE_NAME, "started": self.started,
                "simulated": self.transport.simulated, "provider": self.cfg.provider,
                "classes": sorted(self.bindings), "stats": dict(self.stats)}

    def methods(self) -> dict[str, Callable[..., Any]]:
        return {"health": self.health, "capabilities": self.capabilities,
                "prepare": self.prepare, "send": self.send, "cancel": self.cancel,
                "confirm_served": self.confirm_served}


def make_transport(cfg: Config) -> tuple[Transport, str | None]:
    inference = cfg.inference
    if inference.provider == "fake":
        return FakeTransport(), None
    secret = load_api_key(inference)
    return HttpTransport(inference.base_url, secret or ""), secret


def main(argv: list[str] | None = None) -> int:
    from ..logging_setup import setup_logging
    from ..rpc import RpcServer, read_or_create_token

    ap = argparse.ArgumentParser(prog="remoeba.inference.service")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    log = setup_logging(cfg, SERVICE_NAME)
    transport, secret = make_transport(cfg)
    service = InferenceService(cfg.inference, transport, secret=secret)
    service.start()
    # The only token that opens this server. The supervisor holds it; no role
    # or neuocyte is ever given it, so no mind can make a model call directly.
    token = read_or_create_token(cfg.scope_token_path(SERVICE_NAME))
    server = RpcServer(cfg.supervisor_host, cfg.inference_port, token=token,
                       service_name=SERVICE_NAME)
    server.register_all(service.methods())
    log.info("inference service ready: provider=%s classes=%s simulated=%s",
             cfg.inference.provider, sorted(service.bindings), transport.simulated)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
