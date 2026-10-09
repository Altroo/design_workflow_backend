"""Reuse transport while replacing the reference app's clarification wording."""

from chat_ai_assistant.provider import ChatAIModelService
from chat_ai_assistant.clarifications import MESSAGES

COPY = {
    "fr": {
        "missing_details": "Précisez le projet, la tâche ou la période recherchée (avec l’année).",
        "ambiguous_metric": "Souhaitez-vous connaître le nombre de tâches ou le temps de travail ? Pour quelle période ?",
        "unsupported": "Cette demande n’est pas disponible avec vos accès actuels. Je peux rechercher des projets et des tâches, ou expliquer les fonctionnalités de Design Workflow.",
    },
    "en": {
        "missing_details": "Please specify the project, task or reporting period (including the year).",
        "ambiguous_metric": "Do you mean the number of tasks or working time? For which period?",
        "unsupported": "This request is not available with your current access. I can find projects and tasks or explain Design Workflow features.",
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
