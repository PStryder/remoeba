"""The loopback RPC handshake: a wrong credential is refused, and says so.

The inference service's token is the only thing standing between a mind and
a direct model call, so a refused handshake has to be recognisable as a
refusal -- not as a crash in whoever tried.
"""

from __future__ import annotations

import pytest

from remoeba.rpc import RpcClient, RpcError, RpcServer


@pytest.fixture()
def server():
    srv = RpcServer("127.0.0.1", 0, token="the-right-token", service_name="probe")
    srv.register("ping", lambda: "pong")
    srv.serve_in_thread()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_the_right_token_is_served(server):
    with RpcClient("127.0.0.1", server.port, "the-right-token") as client:
        assert client.call("ping") == "pong"


def test_a_wrong_token_is_refused_as_a_refusal(server):
    client = RpcClient("127.0.0.1", server.port, "not-the-token")
    with pytest.raises(RpcError) as err:
        client.connect()
    assert err.value.details["remote_code"] == "unauthorized"
    client.close()


def test_a_refusal_relayed_across_two_hops_arrives_as_a_refusal():
    """role -> supervisor -> inference is two hops. The middle hop re-raises the
    RpcError it received, which already carries `remote_code`; building the next
    RpcError collided with it and the caller got a TypeError instead."""
    from remoeba.errors import InvalidInput

    inner = RpcServer("127.0.0.1", 0, token="t", service_name="inner")

    def refuse():
        raise InvalidInput("inner refused", why="the reason that must survive")

    inner.register("refuse", refuse)
    inner.serve_in_thread()
    outer = RpcServer("127.0.0.1", 0, token="t", service_name="outer")

    def relay():
        with RpcClient("127.0.0.1", inner.port, "t") as hop:
            return hop.call("refuse")

    outer.register("relay", relay)
    outer.serve_in_thread()
    try:
        with RpcClient("127.0.0.1", outer.port, "t") as client:
            with pytest.raises(RpcError) as err:
                client.call("relay")
        details = err.value.details
        assert details["remote_code"] == "rpc_error"               # the outer hop
        assert details["remote_remote_code"] == "invalid_input"    # the original
        assert details["why"] == "the reason that must survive"
        assert "inner refused" in str(err.value)
    finally:
        for srv in (outer, inner):
            srv.shutdown()
            srv.server_close()
