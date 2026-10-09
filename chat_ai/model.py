"""Reuse transport while replacing the reference app's clarification wording."""

from chat_ai_assistant.provider import ChatAIModelService
from chat_ai_assistant.clarifications import MESSAGES

COPY = {
    "fr": {
        "missing_details": "Précisez le projet, la tâche ou la période recherchée (avec l’année).",
        "ambiguous_metric": "Souhaitez-vous connaître le nombre de tâches ou le temps de travail ? Pour quelle période ?",
        "unsupported": "Je peux vous aider à retrouver des projets, des tâches et des messages, ou à utiliser Design Workflow. Pouvez-vous préciser ce que vous souhaitez faire dans l’application ?",
    },
    "en": {
        "missing_details": "Please specify the project, task or reporting period (including the year).",
        "ambiguous_metric": "Do you mean the number of tasks or working time? For which period?",
        "unsupported": "I can help you find projects, tasks and messages, or use Design Workflow. Could you tell me what you would like to do in the app?",
    },
}


class WorkflowModel(ChatAIModelService):
    def choose(self, *args, **kwargs):
        action, usage = super().choose(*args, **kwargs)
        if action.get("tool") == "clarify":
            for language, reasons in MESSAGES.items():
                for reason, message in reasons.items():
                    if action.get("message") == message:
                        action = {**action, "message": COPY[language][reason]}
        return action, usage
