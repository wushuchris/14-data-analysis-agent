"""Fixed Hugging Face router transport; no automatic retries or redirects."""
import json
import os
import requests
from requests.adapters import HTTPAdapter
from requests.auth import AuthBase

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
    if "type" not in result and result.get("anyOf") in ([{"type": "integer"}, {"type": "number"}],
                               [{"type": "number"}, {"type": "integer"}]):
        # JSON Schema numbers include integers; this union is exactly redundant.
        result.pop("anyOf")
        result["type"] = "number"
    if result.get("type") == "object":
        if result.get("additionalProperties") is not False:
            raise ValueError("closed object required")
        result["required"] = list(result.get("properties", {}))
    return result



def _http_error_code(status, raw):
    """Retain only a safe HTTP status and recognized provider failure category."""
    fallback = {401: "HF_AUTH_FAILED_HTTP_401", 403: "HF_AUTH_FAILED_HTTP_403",
                402: "HF_CREDITS_UNAVAILABLE", 429: "HF_RATE_LIMITED"}.get(
                    status, f"HF_HTTP_{status}" if type(status) is int and 100 <= status <= 599
                    else "HF_HTTP_FAILED")
    if status not in (400, 403, 422):
        return fallback
    try:
        if len(raw) > MAX_WIRE_BYTES:
            return fallback
        text = raw.decode("utf-8")
        if status == 403 and text.strip().lower() == "error code: 1010":
            return "HF_UPSTREAM_CLIENT_REJECTED"
        try:
            payload = _json(text)
        except (ValueError, TypeError, RecursionError):
            return "HF_FORBIDDEN_NON_JSON" if status == 403 else fallback
        if not isinstance(payload, dict):
            return fallback
        detail = payload.get("error")
        if isinstance(detail, dict):
            message, code = detail.get("message"), detail.get("code")
        else:
            message, code = detail, payload.get("code")
        if status in (400, 422):
            categories = {
                "json_validate_failed": "HF_PROVIDER_JSON_GENERATION_FAILED",
                "invalid_json_schema": "HF_PROVIDER_SCHEMA_REJECTED",
                "context_length_exceeded": "HF_PROVIDER_CONTEXT_LIMIT",
                "model_not_found": "HF_PROVIDER_MODEL_UNAVAILABLE",
            }
            if isinstance(code, str) and code in categories:
                return categories[code]
            if isinstance(message, str):
                lowered = message.lower()
                if "json_validate_failed" in lowered or "failed to generate json" in lowered:
                    return "HF_PROVIDER_JSON_GENERATION_FAILED"
                if "invalid schema" in lowered or "invalid_json_schema" in lowered:
                    return "HF_PROVIDER_SCHEMA_REJECTED"
            return fallback
        if code == "inference_provider_permission_required":
            return "HF_RUNTIME_INFERENCE_PERMISSION_DENIED"
        if isinstance(message, str) and (
            "does not have sufficient permissions to call inference providers" in message.lower()
        ):
            return "HF_RUNTIME_INFERENCE_PERMISSION_DENIED"
        if code in ("permission_denied", "access_denied"):
            return "HF_SERVICE_AUTHORIZATION_DENIED"
        return fallback
    except Exception:
        return fallback


class _Bearer(AuthBase):
    """Keep the application credential authoritative over ambient netrc auth."""
    def __init__(self, token):
        self._token = token

    def __call__(self, request):
        request.headers["Authorization"] = "Bearer " + self._token
        return request


def _bounded_body(response, limit):
    data = bytearray()
    for chunk in response.iter_content(chunk_size=4096):
        if chunk:
            data.extend(chunk[:max(0, limit + 1 - len(data))])
        if len(data) > limit:
            break
    return bytes(data)


class _FixedEndpointSession(requests.Session):
    def get_redirect_target(self, response):
        # Do not prepare redirects or preload their response bodies.
        return None


class HFRouterTransport:
    """Fixed Requests client with zero retries and redirects disabled.

    Socket timeouts bound blocking operations, not the whole wall-clock run.
    TLS verification and normal runtime proxy settings remain enabled.
    """
    def __init__(self):
        token = os.environ.get("HF_TOKEN", "").strip()
        if not token or any(char.isspace() for char in token):
            raise AdapterError("HF_TOKEN_UNAVAILABLE")
        self._auth = _Bearer(token)
        self._session = _FixedEndpointSession()
        self._session.mount("https://", HTTPAdapter(max_retries=0))

    def _exchange(self, method, url, timeout, limit, body=None):
        try:
            with self._session.request(method, url, data=body, auth=self._auth,
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    timeout=(timeout, timeout), stream=True, allow_redirects=False) as response:
                status = response.status_code
                raw = _bounded_body(response, limit)
                return status, raw
        except requests.exceptions.Timeout:
            raise TimeoutError("HF_SOCKET_TIMEOUT") from None
        except (requests.exceptions.RequestException, OSError, ValueError):
            raise AdapterError("HF_NETWORK_FAILED") from None
        finally:
            # Release connection pools after every exchange, including failures.
            self._session.close()

    def _credential_validity(self, timeout):
        """One non-inference GET; retain no identity or token metadata."""
        try:
            status, raw = self._exchange("GET", "https://huggingface.co/api/whoami-v2",
                                         min(timeout, 5), 16384)
            if status == 401:
                return False
            if status != 200 or len(raw) > 16384:
                return None
            data = _json(raw.decode("utf-8"))
            if (isinstance(data, dict) and data.get("type") == "user"
                    and isinstance(data.get("auth"), dict)
                    and data["auth"].get("type") == "access_token"):
                return True
        except Exception:
            pass
        return None

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

        status, raw = self._exchange("POST", ENDPOINT, request.timeout_seconds,
                                     MAX_WIRE_BYTES, body)
        if status != 200:
            code = _http_error_code(status, raw)
            if code == "HF_AUTH_FAILED_HTTP_403":
                validity = self._credential_validity(request.timeout_seconds)
                if validity is True:
                    code = "HF_CREDENTIAL_VALID_INFERENCE_FORBIDDEN"
                elif validity is False:
                    code = "HF_RUNTIME_CREDENTIAL_REJECTED"
            raise AdapterError(code)
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
