"""No inference credentials, live network, or credits used by these tests."""
import io
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest
from service_analysis.hf_transport import (
    ENDPOINT, MODEL, MAX_WIRE_BYTES, HFRouterTransport, _NoRedirect, provider_schema,
)
from service_analysis.model_adapter import (
    AdapterError, ModelRequest, FindingsProposal, FollowupProposal,
)
from service_analysis.planning import AnalysisPlan


def request(**overrides):
    values = dict(request_id="synthetic", purpose="initial_plan",
                  instruction="Return a JSON proposal.", payload_json='{"safe":true}',
                  response_schema_json=json.dumps(AnalysisPlan.model_json_schema()),
                  maximum_output_tokens=2000, timeout_seconds=20)
    values.update(overrides)
    return ModelRequest(**values)


def completion(content='{"steps":[]}', finish="stop", **message_fields):
    return json.dumps({"choices": [{"finish_reason": finish, "message": {
        "content": content, **message_fields}}]}).encode()


class FakeOpener:
    def __init__(self, body=None, error=None):
        self.body = completion() if body is None else body
        self.error = error
        self.calls = []

    def open(self, req, timeout):
        self.calls.append((req, timeout))
        if self.error:
            raise self.error
        return io.BytesIO(self.body)


def transport(monkeypatch, **kwargs):
    monkeypatch.setenv("HF_TOKEN", "synthetic-test-credential")
    instance = HFRouterTransport()
    instance._opener = FakeOpener(**kwargs)
    return instance


def test_fixed_route_and_controls(monkeypatch):
    instance = transport(monkeypatch)
    assert instance.complete(request()).finish_reason == "stop"
    wire, timeout = instance._opener.calls[0]
    assert wire.full_url == ENDPOINT
    assert wire.method == "POST"
    assert timeout == 20
    assert wire.get_header("Authorization") == "Bearer synthetic-test-credential"
    body = json.loads(wire.data)
    assert body["model"] == MODEL
    assert body["max_tokens"] == 2000
    assert body["stream"] is False
    assert body["reasoning_effort"] == "low"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert [item["role"] for item in body["messages"]] == ["system", "user"]
    assert "synthetic-test-credential" not in repr(instance)
    assert "synthetic-test-credential" not in wire.data.decode()


@pytest.mark.parametrize("contract", [AnalysisPlan, FindingsProposal, FollowupProposal])
def test_real_schemas_translated_without_mutation(contract):
    original = contract.model_json_schema()
    snapshot = json.dumps(original)
    result = provider_schema(original)
    assert json.dumps(original) == snapshot

    def visit(node):
        assert not {"default", "title", "const", "oneOf", "discriminator"} & node.keys()
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        for key in ("properties", "$defs"):
            for child in node.get(key, {}).values():
                visit(child)
        for child in node.get("anyOf", []):
            visit(child)
        if "items" in node:
            visit(node["items"])
    visit(result)
    if contract is AnalysisPlan:
        assert result["properties"]["steps"]["maxItems"] == 6
        assert result["$defs"]["Period"]["properties"]["start"]["format"] == "date"
        assert result["$defs"]["ComparisonCall"]["properties"]["operation"]["enum"] == ["compare"]
        assert "anyOf" in result["$defs"]["ProposedStep"]["properties"]["call"]


def test_schema_property_named_title_is_preserved():
    result = provider_schema({"type": "object", "additionalProperties": False,
                              "properties": {"title": {"type": "string", "default": "x"}}})
    assert result["properties"] == {"title": {"type": "string"}}
    assert result["required"] == ["title"]


@pytest.mark.parametrize("token", [None, "", "  ", "contains whitespace"])
def test_missing_or_invalid_secret(monkeypatch, token):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    if token is not None:
        monkeypatch.setenv("HF_TOKEN", token)
    with pytest.raises(AdapterError, match="HF_TOKEN_UNAVAILABLE"):
        HFRouterTransport()


@pytest.mark.parametrize("status,code", [(401,"HF_AUTH_FAILED"), (403,"HF_AUTH_FAILED"),
    (402,"HF_CREDITS_UNAVAILABLE"), (429,"HF_RATE_LIMITED"),
    (503,"HF_HTTP_FAILED"), (307,"HF_HTTP_FAILED"), (400,"HF_HTTP_FAILED")])
def test_http_failures_sanitized_and_not_retried(monkeypatch, status, code):
    instance = transport(monkeypatch, error=HTTPError(ENDPOINT, status,
        "private provider diagnostics", {}, io.BytesIO(b"private provider body")))
    with pytest.raises(AdapterError) as error:
        instance.complete(request())
    assert str(error.value) == code
    assert len(instance._opener.calls) == 1


@pytest.mark.parametrize("error", [TimeoutError("private"), URLError(TimeoutError("private"))])
def test_timeout_no_retry(monkeypatch, error):
    instance = transport(monkeypatch, error=error)
    with pytest.raises(TimeoutError, match="HF_SOCKET_TIMEOUT"):
        instance.complete(request())
    assert len(instance._opener.calls) == 1


def test_network_error_sanitized(monkeypatch):
    instance = transport(monkeypatch, error=URLError("private network diagnostics"))
    with pytest.raises(AdapterError, match="HF_NETWORK_FAILED"):
        instance.complete(request())


@pytest.mark.parametrize("body", [b"not JSON", b"\xff", b"{}", b'{"choices":NaN}',
    b'{"choices":[],"choices":[]}', b'{"choices":[]}',
    completion(None), completion(""), completion([], "stop"),
    completion("{}", "tool_calls"), completion("{}", tool_calls=[{"id":"x"}])])
def test_invalid_response_withheld(monkeypatch, body):
    instance = transport(monkeypatch, body=body)
    with pytest.raises(AdapterError, match="HF_RESPONSE_INVALID"):
        instance.complete(request())


@pytest.mark.parametrize("body", [b"x" * (MAX_WIRE_BYTES + 1), completion("x" * 32769)])
def test_oversized_response_withheld(monkeypatch, body):
    with pytest.raises(AdapterError, match="HF_RESPONSE_TOO_LARGE"):
        transport(monkeypatch, body=body).complete(request())


def test_truncation_and_refusal_retained(monkeypatch):
    assert transport(monkeypatch, body=completion("{", "length")).complete(request()).finish_reason == "length"
    result = transport(monkeypatch, body=completion(None, refusal="private refusal")).complete(request())
    assert result.finish_reason == "refusal" and result.text == ""


@pytest.mark.parametrize("values,code", [({"maximum_output_tokens":4001}, "HF_REQUEST_LIMIT_INVALID"),
    ({"timeout_seconds":0}, "HF_REQUEST_LIMIT_INVALID"),
    ({"instruction":"x"*131073}, "HF_REQUEST_TOO_LARGE"),
    ({"payload_json":"not JSON"}, "HF_REQUEST_INVALID"),
    ({"response_schema_json":'{"type":"object"}'}, "HF_REQUEST_INVALID")])
def test_invalid_request_no_network(monkeypatch, values, code):
    instance = transport(monkeypatch)
    with pytest.raises(AdapterError, match=code):
        instance.complete(request(**values))
    assert not instance._opener.calls


def test_redirect_is_never_followed():
    assert _NoRedirect().redirect_request(Request(ENDPOINT), None, 307, "redirect",
                                         {}, "https://another.example") is None
