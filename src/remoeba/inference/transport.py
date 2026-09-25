"""How request bytes reach a provider, and how answers come back.

Two transports behind one interface:

- :class:`HttpTransport` speaks to an OpenAI-format HTTP API (OpenRouter).
  It is the only object anywhere that holds the credential, and it is built
  only inside the inference service process (R1).
- :class:`FakeTransport` answers with OpenRouter-shaped responses from a
  script or a deterministic default. It exists so the rest of the organism can
  be tested without a network, and it says it is simulated.

Both return raw bytes. Deciding what the bytes mean is `wire.classify_http`,
the same code for both, so the fake exercises the real classification.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import threading
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(slots=True)
class RawResponse:
    status: int
    body: bytes
    retry_after: float | None = None


class TransportError(Exception):
    """The exchange did not complete: no status, no body."""

    def __init__(self, message: str, *, cancelled: bool = False) -> None:
        super().__init__(message)
        self.cancelled = cancelled


class Transport(Protocol):
    simulated: bool

    def post_chat(self, body: bytes, *, call_id: str, timeout: float) -> RawResponse: ...
    def get_json(self, path: str, *, timeout: float) -> RawResponse: ...
    def cancel(self, call_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class HttpTransport:
    """Requests to an OpenAI-format endpoint, standard library only.

    The key lives in one attribute and one header and nowhere else. It is not
    part of `repr`, is never logged, and is never returned to a caller.
    """

    simulated = False

    def __init__(self, base_url: str, api_key: str, *,
                 title: str = "Remoeba") -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            raise ValueError(f"not an http(s) base url: {base_url!r}")
        if not api_key:
            raise ValueError("an HTTP transport needs a credential")
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port
        self._prefix = parsed.path.rstrip("/")
        self.__key = api_key
        self._title = title
        self._live: dict[str, http.client.HTTPConnection] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"HttpTransport({self._scheme}://{self._host}{self._prefix})"

    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        cls = (http.client.HTTPSConnection if self._scheme == "https"
               else http.client.HTTPConnection)
        return cls(self._host, self._port, timeout=timeout)

    def _headers(self, *, json_body: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.__key}",
                   "Accept": "application/json",
                   "X-Title": self._title}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def post_chat(self, body: bytes, *, call_id: str, timeout: float) -> RawResponse:
        return self._exchange("POST", "/chat/completions", body, call_id=call_id,
                              timeout=timeout)

    def get_json(self, path: str, *, timeout: float) -> RawResponse:
        return self._exchange("GET", path, None, call_id=None, timeout=timeout)

    def _exchange(self, method: str, path: str, body: bytes | None, *,
                  call_id: str | None, timeout: float) -> RawResponse:
        conn = self._connection(timeout)
        if call_id is not None:
            with self._lock:
                if call_id in self._cancelled:
                    raise TransportError("cancelled before sending", cancelled=True)
                self._live[call_id] = conn
        try:
            conn.request(method, self._prefix + path, body=body,
                         headers=self._headers(json_body=body is not None))
            resp = conn.getresponse()
            data = resp.read()
            return RawResponse(resp.status, data, _retry_after(resp.getheader("Retry-After")))
        except (OSError, http.client.HTTPException) as exc:
            cancelled = call_id is not None and call_id in self._cancelled
            # The exception text can carry a URL but never the header, and it
            # is reduced to its type and message here anyway.
            raise TransportError(f"{type(exc).__name__}: {exc}", cancelled=cancelled) from None
        finally:
            if call_id is not None:
                with self._lock:
                    self._live.pop(call_id, None)
            conn.close()

    def cancel(self, call_id: str) -> bool:
        """Close the call's socket. The provider may still bill what it generated."""
        with self._lock:
            self._cancelled.add(call_id)
            conn = self._live.get(call_id)
        if conn is None or conn.sock is None:
            return conn is not None
        try:
            conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        return True


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # an HTTP-date; the bounded backoff applies instead


# ---------------------------------------------------------------------------
# fake
# ---------------------------------------------------------------------------

SIMULATED_MODEL = "remoeba/simulated"


def fake_endpoint(tag: str, *, provider_name: str = "Simulated",
                  supported: tuple[str, ...] = ("tools", "tool_choice", "max_tokens",
                                                "temperature", "top_p", "seed", "stop"),
                  context_length: int = 131072,
                  max_completion_tokens: int = 16384) -> dict[str, Any]:
    """One entry of an `/endpoints` listing, as OpenRouter shapes it."""
    return {"tag": tag, "provider_name": provider_name, "quantization": "none",
            "context_length": context_length,
            "max_completion_tokens": max_completion_tokens,
            "max_prompt_tokens": None, "supported_parameters": list(supported),
            "pricing": {"prompt": "0", "completion": "0"}}


def fake_completion(content: str | None = "", *, finish: str = "stop",
                    tool_calls: list[dict[str, Any]] | None = None,
                    model: str = SIMULATED_MODEL, cost: float | None = 0.0,
                    prompt_tokens: int = 0, completion_tokens: int = 0,
                    response_id: str = "gen-simulated", **message_extra: Any) -> dict[str, Any]:
    """A chat completion body, as OpenRouter shapes it."""
    message: dict[str, Any] = {"role": "assistant", "content": content, **message_extra}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    usage: dict[str, Any] = {"prompt_tokens": prompt_tokens,
                             "completion_tokens": completion_tokens,
                             "total_tokens": prompt_tokens + completion_tokens}
    if cost is not None:
        usage["cost"] = cost
    return {"id": response_id, "model": model, "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": finish,
                         "native_finish_reason": finish, "message": message}],
            "usage": usage}


Scripted = tuple[int, Any] | Callable[[dict[str, Any]], tuple[int, Any]]


class FakeTransport:
    """Deterministic stand-in with OpenRouter's response shapes.

    `script` holds responses to hand out in order: `(status, payload)`, or a
    function of the parsed request returning one. With the script empty, the
    reply is derived from a hash of the request, so identical requests get
    identical replies and nothing about it resembles a model.

    Every request body it received is kept in `received`, byte for byte,
    because what left is the thing tests most need to see.
    """

    simulated = True

    def __init__(self, *, endpoints: dict[str, list[dict[str, Any]]] | None = None,
                 generations: dict[str, dict[str, Any]] | None = None) -> None:
        self.endpoints = endpoints or {}
        self.generations = generations or {}
        self.script: deque[Scripted] = deque()
        self.received: list[bytes] = []
        self.cancelled: set[str] = set()
        self.block: threading.Event | None = None  # set by tests to hold a call open

    def queue(self, *responses: Scripted) -> None:
        self.script.extend(responses)

    def post_chat(self, body: bytes, *, call_id: str, timeout: float) -> RawResponse:
        self.received.append(body)
        if self.block is not None:
            deadline = time.monotonic() + timeout
            while not self.block.is_set():
                if call_id in self.cancelled:
                    raise TransportError("cancelled", cancelled=True)
                if time.monotonic() > deadline:
                    raise TransportError("timed out")
                time.sleep(0.01)
        if call_id in self.cancelled:
            raise TransportError("cancelled", cancelled=True)
        request = json.loads(body.decode("utf-8"))
        if self.script:
            item = self.script.popleft()
            status, payload = item(request) if callable(item) else item
        else:
            status, payload = 200, self._default(request, body)
        if isinstance(payload, TransportError):
            raise payload
        retry_after = None
        if isinstance(payload, dict) and "_retry_after" in payload:
            payload = dict(payload)
            retry_after = float(payload.pop("_retry_after"))
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return RawResponse(status, data, retry_after)

    @staticmethod
    def _default(request: dict[str, Any], body: bytes) -> dict[str, Any]:
        tag = hashlib.sha256(body).hexdigest()[:12]
        return fake_completion(f"SIMULATED reply {tag}", response_id=f"gen-sim-{tag}",
                               prompt_tokens=len(body) // 4, completion_tokens=4)

    def get_json(self, path: str, *, timeout: float) -> RawResponse:
        if path.startswith("/models/") and path.endswith("/endpoints"):
            model = path[len("/models/"):-len("/endpoints")]
            listed = self.endpoints.get(model)
            if listed is None:
                return RawResponse(404, b'{"error":{"code":404,"message":"model not found"}}')
            return RawResponse(200, json.dumps(
                {"data": {"id": model, "endpoints": listed}}).encode("utf-8"))
        if path.startswith("/generation?id="):
            gen = self.generations.get(urllib.parse.unquote(path.split("=", 1)[1]))
            if gen is None:
                return RawResponse(404, b'{"error":{"code":404,"message":"not found"}}')
            return RawResponse(200, json.dumps({"data": gen}).encode("utf-8"))
        return RawResponse(404, b'{"error":{"code":404,"message":"no such path"}}')

    def cancel(self, call_id: str) -> bool:
        self.cancelled.add(call_id)
        return True
