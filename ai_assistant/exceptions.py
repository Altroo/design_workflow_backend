from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException


class AssistantDisabled(APIException):
    status_code = 503
    default_detail = _("The AI assistant is not available.")
    default_code = "ai_assistant_disabled"


class ModelUnavailable(APIException):
    status_code = 503
    default_detail = _("The AI assistant is temporarily unavailable. Please try again.")
    default_code = "ai_model_unavailable"


class ModelTimeout(APIException):
    status_code = 504
    default_detail = _("The AI assistant took too long to respond. Please try again.")
    default_code = "ai_model_timeout"


class InvalidModelResponse(APIException):
    status_code = 502
    default_detail = _("This suggestion could not be validated. Please try again.")
    default_code = "ai_invalid_response"
