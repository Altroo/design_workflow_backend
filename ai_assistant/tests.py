import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework.throttling import UserRateThrottle
from rest_framework.exceptions import Throttled

from account.models import CustomUser
from .client import AiAssistantClient
from .exceptions import (
    AssistantDisabled,
    InvalidModelResponse,
    ModelTimeout,
    ModelUnavailable,
)
from .serializers import AssistRequestSerializer
from .views import AssistantThrottle

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_ASSISTANT_SERVICE_NAME = "design_workflow"
    settings.AI_ASSISTANT_SERVICE_SECRET = "local-test-secret"
    settings.AI_ASSISTANT_GATEWAY_URL = "http://ai-assistant-gateway:8080"
    settings.AI_ASSISTANT_TIMEOUT_SECONDS = 185
    cache.clear()


def test_signed_gateway_uses_own_identity_exact_body_and_unique_request_id():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(
        {"suggested_text": "Texte corrigé", "secret": "not forwarded"}
    ).encode()
    with patch("ai_assistant.client.request.urlopen", return_value=response) as send:
        client = AiAssistantClient()
        result = client.assist(action="fix_grammar", text="Texte corrige")
        first = send.call_args.args[0]
        client.assist(action="fix_grammar", text="Texte corrige")
        second = send.call_args.args[0]
    headers = {k.lower(): v for k, v in first.header_items()}
    canonical = f"{headers['x-ai-timestamp']}\n{headers['x-ai-service']}\n{headers['x-ai-request-id']}\n{hashlib.sha256(first.data).hexdigest()}"
    assert (
        headers["x-ai-signature"]
        == hmac.new(
            b"local-test-secret", canonical.encode(), hashlib.sha256
        ).hexdigest()
    )
    assert headers["x-ai-service"] == "design_workflow"
    assert first.full_url == "http://ai-assistant-gateway:8080/v1/assist"
    assert first.get_header("X-ai-request-id") != second.get_header("X-ai-request-id")
    assert send.call_args.kwargs["timeout"] == 185
    assert result == {
        "original_text": "Texte corrige",
        "suggested_text": "Texte corrigé",
    }


@pytest.mark.parametrize(
    "action,target",
    [
        ("fix_grammar", None),
        ("professionalize", None),
        ("translate", "en"),
        ("translate", "fr"),
    ],
)
def test_regular_designer_can_request_preview_without_writing_data(action, target):
    api = APIClient()
    url = reverse("ai_assistant:assist")
    body = {"action": action, "text": "Les images sont pret", "context": "comment"}
    if target:
        body["target_language"] = target
    assert api.post(url, body, format="json").status_code in (401, 403)
    user = CustomUser.objects.create_user(
        email="ai-designer@example.test", password="test-password", is_staff=False
    )
    api.force_authenticate(user)
    with patch(
        "ai_assistant.views.AiAssistantClient.assist",
        return_value={
            "original_text": body["text"],
            "suggested_text": "Les images sont prêtes.",
        },
    ) as assist:
        result = api.post(url, body, format="json")
    assert result.status_code == 200
    assert result.data["suggested_text"] == "Les images sont prêtes."
    assert assist.call_count == 1
    from design_workflow.models import Task, Project, TaskComment, ChatMessage

    assert all(
        not model.objects.exists()
        for model in (Task, Project, TaskComment, ChatMessage)
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "change_prices", "text": "Texte"},
        {"action": "fix_grammar", "text": "   "},
        {"action": "fix_grammar", "text": "x" * 5001},
        {"action": "translate", "text": "Texte"},
        {"action": "translate", "text": "Texte", "target_language": "de"},
        {"action": "fix_grammar", "text": "Texte", "context": "<script>"},
    ],
)
def test_rejects_invalid_requests(payload):
    assert not AssistRequestSerializer(data=payload).is_valid()


def test_non_translation_drops_target_and_preserves_spacing():
    serializer = AssistRequestSerializer(
        data={"action": "fix_grammar", "text": " Texte\n", "target_language": "fr"}
    )
    assert serializer.is_valid()
    assert serializer.validated_data["text"] == " Texte\n"
    assert "target_language" not in serializer.validated_data


@pytest.mark.parametrize("flag,key", [(False, "key"), (True, "")])
def test_disabled_or_unconfigured_never_calls_network(settings, flag, key):
    settings.AI_ASSISTANT_ENABLED = flag
    settings.AI_ASSISTANT_SERVICE_SECRET = key
    with (
        patch("ai_assistant.client.request.urlopen") as send,
        pytest.raises(AssistantDisabled),
    ):
        AiAssistantClient().assist(action="fix_grammar", text="Texte")
    send.assert_not_called()


@pytest.mark.parametrize(
    "status,expected",
    [
        (429, Throttled),
        (408, ModelTimeout),
        (504, ModelTimeout),
        (403, ModelUnavailable),
        (502, ModelUnavailable),
    ],
)
def test_gateway_http_failures_do_not_leak_response(status, expected):
    with (
        patch(
            "ai_assistant.client.request.urlopen",
            side_effect=HTTPError("private", status, "private secret", None, None),
        ),
        pytest.raises(expected),
    ):
        AiAssistantClient().assist(action="fix_grammar", text="Texte")


@pytest.mark.parametrize(
    "failure,expected",
    [
        (TimeoutError(), ModelTimeout),
        (URLError(TimeoutError()), ModelTimeout),
        (URLError("dns"), ModelUnavailable),
        (ConnectionError(), ModelUnavailable),
    ],
)
def test_connection_failures(failure, expected):
    with (
        patch("ai_assistant.client.request.urlopen", side_effect=failure),
        pytest.raises(expected),
    ):
        AiAssistantClient().assist(action="fix_grammar", text="Texte")


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[]",
        b"\xff",
        b"{}",
        b'{"suggested_text":null}',
        b'{"suggested_text":" "}',
    ],
)
def test_invalid_gateway_output(raw):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = raw
    with (
        patch("ai_assistant.client.request.urlopen", return_value=response),
        pytest.raises(InvalidModelResponse),
    ):
        AiAssistantClient().assist(action="fix_grammar", text="Texte")


def test_mentions_and_task_links_are_protected_without_sentence_punctuation():
    text = "Merci @maryam et @med.amine pour #business-center."
    with patch.object(
        AiAssistantClient,
        "_post",
        return_value={
            "suggested_text": "Thank you @maryam and @med.amine for #business-center."
        },
    ) as send:
        result = AiAssistantClient().assist(
            action="translate", text=text, target_language="en"
        )
    assert send.call_args.args[0]["protected_terms"] == [
        "@maryam",
        "@med.amine",
        "#business-center",
    ]
    assert send.call_args.args[0]["target_language"] == "en"
    assert "@maryam" in result["suggested_text"]


@pytest.mark.parametrize(
    "suggestion",
    [
        "Thank you @marie for #task",
        "Thank you @maryam",
        "Thank you @maryam @maryam for #task",
        "Thank you @maryam for #task and #extra",
    ],
)
def test_rejects_altered_removed_or_added_references(suggestion):
    with (
        patch.object(
            AiAssistantClient, "_post", return_value={"suggested_text": suggestion}
        ),
        pytest.raises(InvalidModelResponse),
    ):
        AiAssistantClient().assist(
            action="translate", text="Merci @maryam pour #task", target_language="en"
        )


def test_ai_rate_limit_does_not_consume_ordinary_api_quota():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True, pk=99123))
    ordinary, assistant = UserRateThrottle(), AssistantThrottle()
    ordinary_key, ai_key = ordinary.get_cache_key(
        request, None
    ), assistant.get_cache_key(request, None)
    assert ordinary_key != ai_key
    cache.set(ordinary_key, [ordinary.timer()] * 30, 60)
    for _ in range(10):
        assert AssistantThrottle().allow_request(request, None)
    assert not AssistantThrottle().allow_request(request, None)


def test_default_service_identity_belongs_to_design_workflow():
    from design_workflow_backend import settings

    assert settings.AI_ASSISTANT_SERVICE_NAME == "design_workflow"
