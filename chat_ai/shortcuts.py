"""Visible starter questions and optional shortcuts, in both interface languages."""

import re
from chat_ai_assistant.routing import normalized
from chat_ai_assistant.clarifications import message_language
from chat_ai_assistant.contracts import ChatAIError
from .security import is_manager

STARTERS = [
    (
        "Montre mes tâches en cours.",
        "Show my tasks in progress.",
        "search_records",
        {"resource": "task", "mine": True, "status": "in_progress"},
    ),
    (
        "Quelles tâches sont en retard ?",
        "Which tasks are overdue?",
        "search_records",
        {"resource": "task", "overdue": True},
    ),
    (
        "Affiche les projets actifs.",
        "Show active projects.",
        "search_records",
        {"resource": "project", "status": "active"},
    ),
    (
        "Comment créer une tâche ?",
        "How do I create a task?",
        "knowledge",
        {"query": "créer tâche create task project"},
    ),
]
REPORT_STARTER = (
    "Quel est le temps de travail total ?",
    "What is the total working time?",
    "time_report",
    {},
)
MODULES = [
    ("/projets", "project", "Projets", "Projects"),
    ("/taches", "task", "Tâches", "Tasks"),
    ("/messages", "message", "Messages du chat", "Chat messages"),
]
ALIASES = {
    "/projects": "/projets",
    "/tasks": "/taches",
    "/help": "/aide",
    "/summary": "/bilan",
}


def suggestions(user, language="fr"):
    entries = STARTERS + ([REPORT_STARTER] if is_manager(user) else [])
    return [entry[1 if language == "en" else 0] for entry in entries]


def shortcut_catalog(user, language="fr"):
    en = language == "en"
    items = [
        {
            "command": command,
            "title": english if en else french,
            "help": (
                "Type a name or a few words to search."
                if en
                else "Ajoutez un nom ou quelques mots pour rechercher."
            ),
            "example": command + " Atlas",
        }
        for command, _, french, english in MODULES
    ]
    items.append(
        {
            "command": "/aide",
            "title": "Help" if en else "Aide",
            "help": "",
            "example": "/aide",
        }
    )
    if is_manager(user):
        items.append(
            {
                "command": "/bilan",
                "title": "Working time" if en else "Temps de travail",
                "help": (
                    "Native person-time report."
                    if en
                    else "Temps de travail enregistré dans l’application."
                ),
                "example": "/bilan",
            }
        )
    return items


def shortcut_action(text, executor, interface_language="fr"):
    user = executor.authorize()
    for french, english, tool, args in STARTERS + (
        [REPORT_STARTER] if is_manager(user) else []
    ):
        if normalized(text).rstrip(".?! ") in (
            normalized(french).rstrip(".?! "),
            normalized(english).rstrip(".?! "),
        ):
            return {"tool": tool, "arguments": args}
    if not text.startswith("/"):
        return None
    parts = text.split(maxsplit=1)
    command = ALIASES.get(parts[0].casefold(), parts[0].casefold())
    arg = parts[1].strip() if len(parts) > 1 else ""
    language = message_language(text, interface_language)
    if len(arg) > 120:
        return {
            "tool": "clarify",
            "message": (
                "Précisez un nom ou quelques mots (120 caractères maximum)."
                if language == "fr"
                else "Please use a name or a few words (120 characters maximum)."
            ),
        }
    for name, resource, _, _ in MODULES:
        if command == name:
            return {
                "tool": "search_records",
                "arguments": {"resource": resource, "query": arg},
            }
    if command == "/bilan":
        if not is_manager(user):
            raise ChatAIError("PERMISSION_DENIED")
        return {"tool": "time_report", "arguments": {"project_name": arg}}
    if command == "/aide":
        return {
            "tool": "clarify",
            "message": "\n".join(
                x["command"] + " : " + x["title"]
                for x in shortcut_catalog(user, language)
            ),
        }
    return {
        "tool": "clarify",
        "message": (
            "Commande inconnue. Envoyez /aide."
            if language == "fr"
            else "Unknown command. Send /help."
        ),
    }


def reference_action(text, state):
    if not state.get("ids"):
        return None
    match = re.fullmatch(
        r"(?:open (?:the )?|ouvre (?:le |la )?)(first|second|third|premier|premiere|deuxieme|troisieme)(?: one| result| resultat)?[.!?]?",
        normalized(text).strip(),
    )
    if match:
        values = {
            "first": 1,
            "premier": 1,
            "premiere": 1,
            "second": 2,
            "deuxieme": 2,
            "third": 3,
            "troisieme": 3,
        }
        return {
            "tool": "previous_results",
            "arguments": {"operation": "open", "index": values[match[1]]},
        }
    return None


def knowledge_action(text):
    if len(text) <= 120 and re.match(
        r"^(?:how (?:do i|to)|comment (?:creer|utiliser|archiver|ajouter)|que signifie|what does)\b",
        normalized(text),
    ):
        return {"tool": "knowledge", "arguments": {"query": text}}
    return None
