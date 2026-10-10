"""Fixed Hugging Face router transport; no automatic retries or redirects."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

from service_analysis.model_adapter import AdapterError, ModelRequest, ModelResponse

ENDPOINT = "https://router.huggingface.co/v1/chat/completions"
MODEL = "openai/gpt-oss-20b:groq"
MAX_WIRE_BYTES = 98304


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("nonfinite JSON")


def _json(text):
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_nonfinite)


def provider_schema(schema):
    """Translate encoding only; original application schema remains authoritative.

    Defaults/annotations are removed, all object fields become required, const
    becomes enum, and discriminated oneOf becomes anyOf. Date formats and all
    bounds are retained. Never widen a field to nullable or allow extra fields.
    """
    if not isinstance(schema, dict):
        raise ValueError("object schema required")
    result = {}
    for key, value in schema.items():
        if key in {"title", "default", "discriminator"}:
            continue
        if key in {"properties", "$defs"}:
            result[key] = {name: provider_schema(child) for name, child in value.items()}
        elif key in {"anyOf", "oneOf"}:
            result["anyOf"] = [provider_schema(child) for child in value]
        elif key == "items":
            result[key] = provider_schema(value)
        elif key == "const":
            result["enum"] = [value]
        else:
            result[key] = value
    if result.get("type") == "object":
        if result.get("additionalProperties") is not False:
            raise ValueError("closed object required")
        result["required"] = list(result.get("properties", {}))
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HFRouterTransport:
    """Backend-only token; synchronous socket timeout, not a hard wall deadline.

    urllib does not retry HTTP requests. Redirects are rejected so the bearer
    credential cannot be forwarded to a different endpoint. DNS resolution and
    a slow streaming body can outlast the per-blocking-operation socket timeout;
    production would need a worker-level deadline as well.
    """
    def __init__(self):
        token = os.environ.get("HF_TOKEN", "").strip()
        if not token or any(char.isspace() for char in token):
            raise AdapterError("HF_TOKEN_UNAVAILABLE")
        self._token = token
        self._opener = build_opener(_NoRedirect())

    def complete(self, request: ModelRequest) -> ModelResponse:
        if (not 128 <= request.maximum_output_tokens <= 4000
                or not 1 <= request.timeout_seconds <= 60):
            raise AdapterError("HF_REQUEST_LIMIT_INVALID")
        connection_input = (request.instruction + request.payload_json
                            + request.response_schema_json).encode("utf-8")
        if len(connection_input) > 131072:
            raise AdapterError("HF_REQUEST_TOO_LARGE")
        try:
            schema = provider_schema(_json(request.response_schema_json))
            if schema.get("type") != "object":
                raise ValueError("root object required")
            _json(request.payload_json)
            body = json.dumps({
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": request.instruction},
                    {"role": "user", "content": request.payload_json},
                ],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "analysis_proposal", "strict": True, "schema": schema,
                }},
                "max_tokens": request.maximum_output_tokens,
                "temperature": 0,
                "stream": False,
                "reasoning_effort": "low",
            }, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, RecursionError, AttributeError):
            raise AdapterError("HF_REQUEST_INVALID") from None
        wire_request = Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": "Bearer " + self._token,
            "Content-Type": "application/json", "Accept": "application/json",
        })
        try:
            with self._opener.open(wire_request, timeout=request.timeout_seconds) as response:
                raw = response.read(MAX_WIRE_BYTES + 1)
        except HTTPError as exc:
            code = {401: "HF_AUTH_FAILED", 403: "HF_AUTH_FAILED",
                    402: "HF_CREDITS_UNAVAILABLE", 429: "HF_RATE_LIMITED"}.get(
                        exc.code, "HF_HTTP_FAILED")
            exc.close()
            raise AdapterError(code) from None
        except TimeoutError:
            raise TimeoutError("HF_SOCKET_TIMEOUT") from None
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError("HF_SOCKET_TIMEOUT") from None
            raise AdapterError("HF_NETWORK_FAILED") from None
        except (OSError, ValueError):
            raise AdapterError("HF_NETWORK_FAILED") from None
        if len(raw) > MAX_WIRE_BYTES:
            raise AdapterError("HF_RESPONSE_TOO_LARGE")
        try:
            parsed = _json(raw.decode("utf-8"))
            choices = parsed["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("one choice required")
            choice = choices[0]
            message = choice["message"]
            if message.get("refusal") or choice["finish_reason"] == "content_filter":
                return ModelResponse(text="", finish_reason="refusal")
            finish = choice["finish_reason"]
            if finish not in {"stop", "length"} or message.get("tool_calls"):
                raise ValueError("unexpected completion")
            text = message["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("missing content")
            if len(text.encode("utf-8")) > 32768:
                raise AdapterError("HF_RESPONSE_TOO_LARGE")
            return ModelResponse(text=text, finish_reason=finish)
        except AdapterError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise AdapterError("HF_RESPONSE_INVALID") from None
