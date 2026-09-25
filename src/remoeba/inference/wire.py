"""The request and response format between the Harness and an OpenAI-format provider.

Pure: no network, no credential, no state. Everything here can be imported by
any process, because nothing here can send anything.

Three jobs, each the single place it happens:

1. **Build** a request body from a model binding, a message list, a tool list
   and a profile's effective settings -- and pin it (docs/PORTING.md,
   OpenRouter): one endpoint, fallbacks off, every parameter required,
   data collection denied unless the class says otherwise.
2. **Check** a body before it leaves, against the binding and against the
   pinned endpoint's declared capabilities (R7). The service checks again at
   send time whoever built the body, because the body that leaves is the one
   that matters.
3. **Classify** whatever came back into one outcome kind, never collapsing a
   rate limit, a credit exhaustion, a filter or a provider error into "the
   model stopped" (I66, R5, R9).

Structure is the messages array (R6). Nothing here renders a chat template,
splits text into messages, or looks inside a message's content: content is a
string that is carried, never parsed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..errors import CapabilityUnsupported, InvalidInput
from ..promptlib.model import BACKEND_ARGUMENT, validate_model_vars

# ---------------------------------------------------------------------------
# bindings and capabilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelBinding:
    """What a model class resolved to: the model and the one endpoint serving it."""

    model_class: str
    model: str
    endpoint: str
    data_collection: str = "deny"
    zdr: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EndpointCapabilities:
    """What the pinned endpoint declares it supports.

    Read from the endpoint, never from the model-level listing: the model
    listing is a union across every deployment of the model, so it can claim
    `tools` for a model one of whose endpoints has none.
    """

    endpoint: str
    provider_name: str
    quantization: str | None
    context_length: int | None
    max_completion_tokens: int | None
    max_prompt_tokens: int | None
    supported_parameters: frozenset[str]
    pricing: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["supported_parameters"] = sorted(self.supported_parameters)
        return d


def endpoint_capabilities(endpoints_payload: dict[str, Any],
                          endpoint: str) -> EndpointCapabilities:
    """Find the pinned endpoint in `/models/<id>/endpoints` output.

    Absent is a refusal, not a fallback to another endpoint of the same model.
    """
    data = endpoints_payload.get("data") if isinstance(endpoints_payload, dict) else None
    listed = (data or {}).get("endpoints") if isinstance(data, dict) else None
    if not isinstance(listed, list):
        raise InvalidInput("the provider's endpoint listing has no endpoints",
                           endpoint=endpoint)
    for ep in listed:
        if isinstance(ep, dict) and ep.get("tag") == endpoint:
            return EndpointCapabilities(
                endpoint=endpoint,
                provider_name=str(ep.get("provider_name") or ""),
                quantization=ep.get("quantization"),
                context_length=_int_or_none(ep.get("context_length")),
                max_completion_tokens=_int_or_none(ep.get("max_completion_tokens")),
                max_prompt_tokens=_int_or_none(ep.get("max_prompt_tokens")),
                supported_parameters=frozenset(
                    p for p in (ep.get("supported_parameters") or [])
                    if isinstance(p, str)),
                pricing=dict(ep.get("pricing") or {}),
            )
    raise InvalidInput(
        "the pinned endpoint is not offered for this model",
        endpoint=endpoint,
        offered=sorted(str(ep.get("tag")) for ep in listed if isinstance(ep, dict)))


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ---------------------------------------------------------------------------
# building a request
# ---------------------------------------------------------------------------

MESSAGE_ROLES = ("system", "user", "assistant", "tool")
_MESSAGE_KEYS = {
    "system": {"role", "content"},
    "user": {"role", "content"},
    "assistant": {"role", "content", "tool_calls"},
    "tool": {"role", "content", "tool_call_id"},
}

# Every top-level key a body may carry. A key outside this set is refused at
# send time: an unexpected field is either a parameter nobody checked against
# the endpoint, or a routing override nobody decided on.
BODY_KEYS = frozenset({"model", "messages", "tools", "provider"}
                      | set(BACKEND_ARGUMENT.values()))


def provider_routing(binding: ModelBinding) -> dict[str, Any]:
    """The routing block that makes a binding mean one deployment.

    Each field overrides an OpenRouter default that would otherwise break an
    invariant (docs/PORTING.md, OpenRouter):

    - `order` + `only` + `allow_fallbacks: False` -- the call runs on the
      pinned endpoint or not at all, so the model a mind is bound to (I15,
      I57) is the model that answers;
    - `require_parameters: True` -- an endpoint that lacks a parameter is
      excluded instead of silently ignoring it (I135);
    - `data_collection` -- `deny` unless the class states otherwise (R2).
    """
    routing: dict[str, Any] = {
        "order": [binding.endpoint],
        "only": [binding.endpoint],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": binding.data_collection,
    }
    if binding.zdr:
        routing["zdr"] = True
    return routing


def build_body(binding: ModelBinding, messages: list[dict[str, Any]], *,
               settings: dict[str, Any],
               tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A request body for one call.

    `settings` are a profile's *effective settings* in profile names -- the
    same values the incarnation binding records -- and are translated here and
    nowhere else, so what the record says was applied is what the request
    carries (I135). `max_output_tokens` is required: every mind has a ceiling
    (I110), and an absent one would hand the choice to the provider.
    """
    checked = validate_model_vars(dict(settings or {}))
    if "max_output_tokens" not in checked:
        raise InvalidInput("a request needs max_output_tokens: every call has a "
                           "ceiling, and leaving it out hands it to the provider")
    body: dict[str, Any] = {
        "model": binding.model,
        "messages": check_messages(messages),
    }
    for name, value in checked.items():
        body[BACKEND_ARGUMENT[name]] = value
    if tools:
        body["tools"] = check_tools(tools)
    body["provider"] = provider_routing(binding)
    return body


def check_messages(messages: Any) -> list[dict[str, Any]]:
    """The message list must already be well formed; nothing is repaired here.

    Content is carried as a string and never inspected (R6).
    """
    if not isinstance(messages, list) or not messages:
        raise InvalidInput("messages must be a non-empty list")
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise InvalidInput(f"messages[{i}] must be an object")
        role = msg.get("role")
        if role not in MESSAGE_ROLES:
            raise InvalidInput(f"messages[{i}].role must be one of {MESSAGE_ROLES}",
                               got=role)
        extra = set(msg) - _MESSAGE_KEYS[role]
        if extra:
            raise InvalidInput(f"messages[{i}] ({role}) has unexpected keys",
                               keys=sorted(extra))
        content = msg.get("content")
        if role == "assistant":
            calls = msg.get("tool_calls")
            if content is None and not calls:
                raise InvalidInput(f"messages[{i}] (assistant) has neither content "
                                   "nor tool_calls")
            if content is not None and not isinstance(content, str):
                raise InvalidInput(f"messages[{i}].content must be a string")
            if calls is not None:
                _check_tool_calls(calls, f"messages[{i}].tool_calls")
        elif not isinstance(content, str):
            raise InvalidInput(f"messages[{i}].content must be a string")
        if role == "tool" and not (isinstance(msg.get("tool_call_id"), str)
                                   and msg["tool_call_id"]):
            raise InvalidInput(f"messages[{i}] (tool) needs a tool_call_id")
    return messages


def _check_tool_calls(calls: Any, where: str) -> None:
    if not isinstance(calls, list) or not calls:
        raise InvalidInput(f"{where} must be a non-empty list")
    for j, call in enumerate(calls):
        fn = call.get("function") if isinstance(call, dict) else None
        if not (isinstance(call, dict) and isinstance(call.get("id"), str)
                and call.get("type") == "function" and isinstance(fn, dict)
                and isinstance(fn.get("name"), str)
                and isinstance(fn.get("arguments"), str)):
            raise InvalidInput(f"{where}[{j}] must be "
                               '{"id", "type": "function", "function": {"name", "arguments"}}')


def check_tools(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        raise InvalidInput("tools must be a list")
    names: set[str] = set()
    for i, tool in enumerate(tools):
        fn = tool.get("function") if isinstance(tool, dict) else None
        if not (isinstance(tool, dict) and tool.get("type") == "function"
                and isinstance(fn, dict) and isinstance(fn.get("name"), str)
                and fn["name"] and isinstance(fn.get("parameters", {}), dict)):
            raise InvalidInput(f"tools[{i}] must be "
                               '{"type": "function", "function": {"name", "parameters"}}')
        if fn["name"] in names:
            raise InvalidInput("a tool name appears twice", name=fn["name"])
        names.add(fn["name"])
    return tools


# ---------------------------------------------------------------------------
# checking a body before it leaves
# ---------------------------------------------------------------------------


def check_body(body: Any, binding: ModelBinding) -> dict[str, Any]:
    """Refuse any body that does not mean exactly this binding.

    Run at send time on whatever arrives, whoever built it: the pin, the
    parameter requirement and the data-collection setting are properties of
    the bytes that leave, so that is where they are checked.
    """
    if not isinstance(body, dict):
        raise InvalidInput("a request body must be a JSON object")
    extra = set(body) - BODY_KEYS
    if extra:
        raise InvalidInput("the request body carries keys nothing checks",
                           keys=sorted(extra))
    if body.get("model") != binding.model:
        raise InvalidInput("the request names a different model than its class",
                           model_class=binding.model_class, bound=binding.model,
                           requested=body.get("model"))
    if body.get("provider") != provider_routing(binding):
        raise InvalidInput(
            "the request's provider routing is not this class's pin",
            expected=provider_routing(binding), got=body.get("provider"))
    check_messages(body.get("messages"))
    if "tools" in body:
        check_tools(body["tools"])
    if not isinstance(body.get("max_tokens"), int):
        raise InvalidInput("the request carries no max_tokens ceiling")
    return body


def check_capabilities(body: dict[str, Any], caps: EndpointCapabilities) -> None:
    """Refuse what the pinned endpoint does not declare (R7).

    `require_parameters` would make OpenRouter refuse too, but only after the
    request had left the machine and been recorded as sent. Refusing here
    means the refusal is ours, before anything crosses.
    """
    wanted = [key for key in body if key in set(BACKEND_ARGUMENT.values())]
    if "tools" in body:
        wanted.append("tools")
    missing = sorted(p for p in wanted if p not in caps.supported_parameters)
    if missing:
        raise CapabilityUnsupported("the pinned endpoint does not support these parameters",
                           endpoint=caps.endpoint, unsupported=missing,
                           supported=sorted(caps.supported_parameters))
    cap = caps.max_completion_tokens
    if cap is not None and body["max_tokens"] > cap:
        # Refused, never clamped: a silent clamp is how a governed ceiling
        # became a smaller one with nobody told (I110).
        raise CapabilityUnsupported("max_tokens exceeds what the pinned endpoint allows",
                           endpoint=caps.endpoint, max_tokens=body["max_tokens"],
                           endpoint_max_completion_tokens=cap)


def require_tools(caps: EndpointCapabilities) -> None:
    """Native tool calling is required of every model class (decision 3)."""
    if "tools" not in caps.supported_parameters:
        raise CapabilityUnsupported("the pinned endpoint does not support native tool "
                           "calling, which every model class requires",
                           endpoint=caps.endpoint)


# ---------------------------------------------------------------------------
# canonical bytes
# ---------------------------------------------------------------------------


def canonical_bytes(body: dict[str, Any]) -> bytes:
    """The exact bytes that are committed and then sent.

    One serialisation, used for both, so the digest the record holds names the
    bytes that left (R2).
    """
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# classifying what came back
# ---------------------------------------------------------------------------


class Outcome:
    """Every kind a call can end in. None of them is a synonym for another."""

    # a completion arrived
    MODEL_STOP = "model_stop"                 # finish_reason "stop"
    TOOL_CALLS = "tool_calls"                 # the model asked for tools
    MAX_OUTPUT = "max_output_tokens"          # finish_reason "length"
    CONTENT_FILTER = "content_filter"         # R9: a filter ended it
    REFUSED = "refused"                       # R9: the model returned a refusal
    PROVIDER_ERROR = "provider_error"         # finish_reason "error": NOT a stop
    UNKNOWN_FINISH = "unknown_finish"         # recorded, never guessed at
    # the call itself failed
    RATE_LIMITED = "rate_limited"             # 429
    CREDITS_EXHAUSTED = "credits_exhausted"   # 402: OpenRouter's, not our ceiling
    CONTEXT_PRESSURE = "context_pressure"     # the prompt did not fit
    AUTH_FAILED = "auth_failed"               # 401 / 403
    INVALID_REQUEST = "invalid_request"       # 400 for any other reason
    PROVIDER_UNAVAILABLE = "provider_unavailable"  # 5xx, 404, timeout, refused
    MALFORMED_RESPONSE = "malformed_response"  # 200 we could not read
    CANCELLED = "cancelled"

    COMPLETED = frozenset({MODEL_STOP, TOOL_CALLS, MAX_OUTPUT})
    RETRYABLE = frozenset({RATE_LIMITED, PROVIDER_UNAVAILABLE})


_FINISH = {
    "stop": Outcome.MODEL_STOP,
    "length": Outcome.MAX_OUTPUT,
    "tool_calls": Outcome.TOOL_CALLS,
    "content_filter": Outcome.CONTENT_FILTER,
    "error": Outcome.PROVIDER_ERROR,
}

# Matched on wording, because a context-length refusal carries no code of its
# own across providers. That is fragile (I135), so the outcome says it was
# decided this way.
_CONTEXT_WORDING = re.compile(
    r"context[ _-]?(length|window)|maximum context|too many tokens|"
    r"prompt is too long|reduce the length", re.IGNORECASE)


def classify_http(status: int, body: bytes,
                  retry_after: float | None = None) -> dict[str, Any]:
    """Turn one HTTP exchange into an outcome record."""
    if status == 200:
        return _classify_completion(body)
    err = _error_payload(body)
    detail: dict[str, Any] = {"http_status": status, "error": err}
    if retry_after is not None:
        detail["retry_after_seconds"] = retry_after
    message = str((err or {}).get("message") or "")
    if status == 402:
        kind = Outcome.CREDITS_EXHAUSTED
    elif status == 429:
        kind = Outcome.RATE_LIMITED
    elif status in (401, 403):
        kind = Outcome.AUTH_FAILED
    elif status in (400, 413) and _CONTEXT_WORDING.search(message):
        kind = Outcome.CONTEXT_PRESSURE
        detail["decided_by"] = "error message wording"
    elif status in (400, 422):
        kind = Outcome.INVALID_REQUEST
    elif status == 404 or status == 408 or status >= 500:
        kind = Outcome.PROVIDER_UNAVAILABLE
    else:
        kind = Outcome.INVALID_REQUEST
    return {"kind": kind, **detail}


def _error_payload(body: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"message": body[:2000].decode("utf-8", "replace"), "unparsed": True}
    err = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(err, dict):
        return {k: err.get(k) for k in ("code", "message", "metadata") if k in err}
    return {"message": str(parsed)[:2000], "unparsed": True}


def _classify_completion(body: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(body.decode("utf-8"))
        choice = parsed["choices"][0]
        message = choice.get("message") or {}
    except (UnicodeDecodeError, ValueError, KeyError, IndexError, TypeError,
            AttributeError):
        return {"kind": Outcome.MALFORMED_RESPONSE, "http_status": 200}
    if isinstance(parsed.get("error"), dict):
        # A 200 carrying an error is still an error.
        return {"kind": Outcome.PROVIDER_ERROR, "http_status": 200,
                "error": _error_payload(body)}

    native = choice.get("native_finish_reason")
    finish = choice.get("finish_reason")
    calls, call_problems = _read_tool_calls(message.get("tool_calls"))
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal:
        kind = Outcome.REFUSED
    elif calls and finish in ("tool_calls", "stop", None):
        # Some upstreams report "stop" beside tool calls; the calls are the fact.
        kind = Outcome.TOOL_CALLS
    else:
        kind = _FINISH.get(finish, Outcome.UNKNOWN_FINISH)
    usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    cost = usage.get("cost")
    content = message.get("content")
    return {
        "kind": kind,
        "http_status": 200,
        "response_id": parsed.get("id"),
        "reported_model": parsed.get("model"),
        "system_fingerprint": parsed.get("system_fingerprint"),
        "finish_reason": finish,
        "native_finish_reason": native,
        "content": content if isinstance(content, str) else None,
        "tool_calls": calls,
        "tool_call_problems": call_problems,
        "refusal": refusal if isinstance(refusal, str) and refusal else None,
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cached_tokens": prompt_details.get("cached_tokens"),
            "reasoning_tokens": completion_details.get("reasoning_tokens"),
        },
        # Documented as optional. Absent is "unpriced", never zero (R4).
        "cost": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
        "cost_source": "provider" if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
    }


def _read_tool_calls(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Tool calls as the model sent them, and what is wrong with any of them.

    A call whose arguments are not a JSON object is kept, not dropped, and
    reported: I115 says a malformed call is refused *to the model* with the
    reason, which needs the call.
    """
    if not isinstance(raw, list):
        return [], []
    calls: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    for i, call in enumerate(raw):
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
            problems.append({"index": i, "problem": "no function name", "raw": call})
            continue
        args_text = fn.get("arguments")
        entry = {"id": call.get("id"), "name": fn["name"],
                 "arguments_text": args_text if isinstance(args_text, str) else None,
                 "arguments": None}
        try:
            args = json.loads(args_text) if isinstance(args_text, str) and args_text else {}
            if not isinstance(args, dict):
                raise ValueError("not an object")
            entry["arguments"] = args
        except ValueError as exc:
            problems.append({"index": i, "name": fn["name"],
                             "problem": f"arguments are not a JSON object: {exc}"})
        calls.append(entry)
    return calls, problems
