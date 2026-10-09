"""Reuse the private signed AI gateway used by Contrat and Reservation."""

import hashlib
import hmac
import json
import re
import time
from collections import Counter
from urllib import error, request
from uuid import uuid4

from django.conf import settings
from rest_framework.exceptions import Throttled

from .exceptions import (
    AssistantDisabled,
    InvalidModelResponse,
    ModelTimeout,
    ModelUnavailable,
)

# These tokens link to people, tasks and projects. They are not prose to translate.
REFERENCE_TOKEN = re.compile(r"(?<!\w)[@#]\w(?:[\w.-]*\w)?", re.UNICODE)


class AiAssistantClient:
    def _post(self, payload):
        if (
            not settings.AI_ASSISTANT_ENABLED
            or not settings.AI_ASSISTANT_SERVICE_SECRET
        ):
            raise AssistantDisabled()
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        request_id = uuid4().hex
        service = settings.AI_ASSISTANT_SERVICE_NAME
        canonical = (
            f"{timestamp}\n{service}\n{request_id}\n{hashlib.sha256(body).hexdigest()}"
        )
        signature = hmac.new(
            settings.AI_ASSISTANT_SERVICE_SECRET.encode(),
            canonical.encode(),
            hashlib.sha256,
        ).hexdigest()
        http_request = request.Request(
            f"{settings.AI_ASSISTANT_GATEWAY_URL.rstrip('/')}/v1/assist",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-AI-Service": service,
                "X-AI-Timestamp": timestamp,
                "X-AI-Request-ID": request_id,
                "X-AI-Signature": signature,
            },
            method="POST",
        )
        try:
            with request.urlopen(
                http_request, timeout=settings.AI_ASSISTANT_TIMEOUT_SECONDS
            ) as response:
                result = json.loads(response.read(1024 * 1024).decode())
        except TimeoutError as exc:
            raise ModelTimeout() from exc
        except error.HTTPError as exc:
            if exc.code == 429:
                raise Throttled(wait=60) from exc
            if exc.code in (408, 504):
                raise ModelTimeout() from exc
            raise ModelUnavailable() from exc
        except error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ModelTimeout() from exc
            raise ModelUnavailable() from exc
        except OSError as exc:
            raise ModelUnavailable() from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidModelResponse() from exc
        if not isinstance(result, dict):
            raise InvalidModelResponse()
        return result

    def assist(
        self,
        *,
        action,
        text,
        source_language="auto",
        target_language=None,
        context="other",
    ):
        references = Counter(REFERENCE_TOKEN.findall(text))
        payload = {
            "action": action,
            "text": text,
            "source_language": source_language,
            "context": context,
            "protected_terms": list(references)[:100],
        }
        if target_language:
            payload["target_language"] = target_language
        result = self._post(payload)
        suggestion = result.get("suggested_text")
        if (
            not isinstance(suggestion, str)
            or not suggestion.strip()
            or len(suggestion) > 20000
            or Counter(REFERENCE_TOKEN.findall(suggestion)) != references
        ):
            raise InvalidModelResponse()
        # Never pass arbitrary gateway fields or save content to application records.
        return {"original_text": text, "suggested_text": suggestion}
