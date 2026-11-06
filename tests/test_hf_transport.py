"""No inference credentials, live network, or credits used by these tests."""
import io
import json
import requests
from requests import Response
from requests.adapters import HTTPAdapter

import pytest
from service_analysis.hf_transport import (
    ENDPOINT, MODEL, MAX_WIRE_BYTES, HFRouterTransport, provider_schema,
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


class RecordingAdapter(HTTPAdapter):
    """Exercise real Requests preparation without opening a network socket."""
    def __init__(self, body=None, status=200, error=None, sequence=None):
        super().__init__(max_retries=0)
        self.body = completion() if body is None else body
        self.status = status
        self.error = error
        self.sequence = list(sequence or [])
        self.calls = []
        self.close_count = 0

    def send(self, wire, **kwargs):
        self.calls.append((wire, kwargs["timeout"]))
        assert kwargs["stream"] is True and kwargs["verify"] is True
        if self.error:
            raise self.error
        status, body = self.sequence.pop(0) if self.sequence else (self.status, self.body)
        result = Response()
        result.status_code = status
        result.raw = io.BytesIO(body)
        result.url = wire.url
        result.request = wire
        if 300 <= status < 400:
            result.headers["Location"] = "https://another.example/blocked"
        return result

    def close(self):
        self.close_count += 1
        super().close()


def transport(monkeypatch, **kwargs):
    monkeypatch.setenv("HF_TOKEN", "synthetic-test-credential")
    instance = HFRouterTransport()
    adapter = RecordingAdapter(**kwargs)
    instance._session.mount("https://", adapter)
    instance._test_adapter = adapter
    return instance


def test_fixed_route_and_controls(monkeypatch):
    instance = transport(monkeypatch)
    assert instance.complete(request()).finish_reason == "stop"
    wire, timeout = instance._test_adapter.calls[0]
    assert wire.url == ENDPOINT
    assert wire.method == "POST"
    assert wire.headers["User-Agent"].startswith("python-requests/")
    assert instance._test_adapter.max_retries.total == 0
    assert instance._test_adapter.close_count == 1
    assert timeout == (20, 20)
    assert wire.headers.get("Authorization") == "Bearer synthetic-test-credential"
    body = json.loads(wire.body)
    assert body["model"] == MODEL
    assert body["max_tokens"] == 2000
    assert body["stream"] is False
    assert body["reasoning_effort"] == "low"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert [item["role"] for item in body["messages"]] == ["system", "user"]
    assert "synthetic-test-credential" not in repr(instance)
    assert "synthetic-test-credential" not in wire.body.decode()


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


@pytest.mark.parametrize("status,code", [(401,"HF_AUTH_FAILED_HTTP_401"), (403,"HF_AUTH_FAILED_HTTP_403"),
    (402,"HF_CREDITS_UNAVAILABLE"), (429,"HF_RATE_LIMITED"),
    (503,"HF_HTTP_503"), (307,"HF_HTTP_307"), (400,"HF_HTTP_400")])
def test_http_failures_sanitized_and_not_retried(monkeypatch, status, code):
    instance = transport(monkeypatch, status=status, body=b'{"error":"private provider body"}')
    with pytest.raises(AdapterError) as error:
        instance.complete(request())
    assert str(error.value) == code
    assert len([req for req, _ in instance._test_adapter.calls if req.method == "POST"]) == 1


@pytest.mark.parametrize("error", [requests.exceptions.ConnectTimeout("private"), requests.exceptions.ReadTimeout("private")])
def test_timeout_no_retry(monkeypatch, error):
    instance = transport(monkeypatch, error=error)
    with pytest.raises(TimeoutError, match="HF_SOCKET_TIMEOUT"):
        instance.complete(request())
    assert len([req for req, _ in instance._test_adapter.calls if req.method == "POST"]) == 1


def test_network_error_sanitized(monkeypatch):
    instance = transport(monkeypatch, error=requests.exceptions.ConnectionError("private network diagnostics"))
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
    assert not instance._test_adapter.calls


def test_redirect_is_never_followed(monkeypatch):
    instance = transport(monkeypatch, status=307, body=b"synthetic redirect")
    with pytest.raises(AdapterError, match="HF_HTTP_307"):
        instance.complete(request())
    assert len(instance._test_adapter.calls) == 1
    assert instance._test_adapter.calls[0][0].url == ENDPOINT
    assert instance._session.get_redirect_target(None) is None


@pytest.mark.parametrize("body,expected", [
    (b'{"error":"This authentication method does not have sufficient permissions to call Inference Providers on behalf of a synthetic user."}',
     "HF_RUNTIME_INFERENCE_PERMISSION_DENIED"),
    (b'{"error":{"code":"inference_provider_permission_required","message":"synthetic private diagnostic"}}',
     "HF_RUNTIME_INFERENCE_PERMISSION_DENIED"),
    (b'{"error":{"code":"permission_denied","message":"synthetic private diagnostic"}}',
     "HF_SERVICE_AUTHORIZATION_DENIED"),
    (b"error code: 1010", "HF_UPSTREAM_CLIENT_REJECTED"),
    (b"<html>synthetic forbidden page</html>", "HF_FORBIDDEN_NON_JSON"),
    (b'{"error":"unknown synthetic diagnostic"}', "HF_AUTH_FAILED_HTTP_403"),
    (b'{"error":[]}', "HF_AUTH_FAILED_HTTP_403"),
    (b"x" * (MAX_WIRE_BYTES + 1), "HF_AUTH_FAILED_HTTP_403"),
])
def test_forbidden_diagnostic_is_bounded_sanitized_and_never_retried(monkeypatch, body, expected):
    instance = transport(monkeypatch, status=403, body=body)
    with pytest.raises(AdapterError) as error:
        instance.complete(request())
    assert str(error.value) == expected
    assert len([req for req, _ in instance._test_adapter.calls if req.method == "POST"]) == 1
    assert "synthetic" not in str(error.value)


@pytest.mark.parametrize("probe_body,probe_status,expected", [
    ({"type":"user","auth":{"type":"access_token","accessToken":{"displayName":"synthetic-private-label"}},
      "email":"synthetic-private@example.invalid"}, 200, "HF_CREDENTIAL_VALID_INFERENCE_FORBIDDEN"),
    ({}, 401, "HF_RUNTIME_CREDENTIAL_REJECTED"),
    ({}, 403, "HF_AUTH_FAILED_HTTP_403"),
    ({"type":"user"}, 200, "HF_AUTH_FAILED_HTTP_403"),
])
def test_forbidden_identity_probe_reveals_only_validity(monkeypatch, probe_body, probe_status, expected):
    instance = transport(monkeypatch, sequence=[
        (403, b'{"error":"unknown denial"}'),
        (probe_status, json.dumps(probe_body).encode()),
    ])
    with pytest.raises(AdapterError) as error:
        instance.complete(request())
    assert str(error.value) == expected
    assert [wire.method for wire, _ in instance._test_adapter.calls] == ["POST", "GET"]
    probe, timeout = instance._test_adapter.calls[1]
    assert probe.url == "https://huggingface.co/api/whoami-v2"
    assert timeout == (5, 5)
    assert probe.headers["Authorization"] == "Bearer synthetic-test-credential"
    assert "synthetic" not in str(error.value)


def test_ambient_netrc_cannot_replace_runtime_credential(monkeypatch):
    def forbidden_lookup(*args, **kwargs):
        raise AssertionError("Ambient credentials must not be consulted")
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", forbidden_lookup)
    instance = transport(monkeypatch)
    instance.complete(request())
    assert instance._test_adapter.calls[0][0].headers["Authorization"] == "Bearer synthetic-test-credential"


def test_repeated_model_rounds_release_connections(monkeypatch):
    instance = transport(monkeypatch)
    for _ in range(3):
        assert instance.complete(request()).finish_reason == "stop"
    assert len(instance._test_adapter.calls) == 3
    assert instance._test_adapter.close_count == 3


@pytest.mark.parametrize("status,body,expected", [
    (400, {"error":{"code":"json_validate_failed","message":"Failed to generate JSON.",
                     "failed_generation":"synthetic private output " * 300}},
     "HF_PROVIDER_JSON_GENERATION_FAILED"),
    (400, {"error":{"code":"invalid_json_schema","message":"synthetic private schema"}},
     "HF_PROVIDER_SCHEMA_REJECTED"),
    (422, {"error":{"code":"context_length_exceeded","message":"synthetic private prompt"}},
     "HF_PROVIDER_CONTEXT_LIMIT"),
    (400, {"error":"synthetic opaque diagnostic"}, "HF_HTTP_400"),
])
def test_finding_http_failure_category_does_not_expose_generated_text(monkeypatch, status, body, expected):
    instance = transport(monkeypatch, status=status, body=json.dumps(body).encode())
    with pytest.raises(AdapterError) as error:
        instance.complete(request(purpose="findings"))
    assert str(error.value) == expected
    assert "synthetic" not in str(error.value)
    assert len(instance._test_adapter.calls) == 1
