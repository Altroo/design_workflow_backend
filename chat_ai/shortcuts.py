"""Visible starter questions and optional shortcuts, in both interface languages."""

import re
from chat_ai_assistant.routing import normalized
from chat_ai_assistant.clarifications import message_language
from chat_ai_assistant.contracts import ChatAIError
from .security import capabilities, is_admin

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
MODULE_HELP = {
    "project": (
        "Recherchez un projet par son nom ou sa description.",
        "Find a project by its name or description.",
        "Atlas",
        "Atlas",
    ),
    "task": (
        "Recherchez une carte par son titre ou sa description.",
        "Find a card by its title or description.",
        "Moodboard",
        "Moodboard",
    ),
    "message": (
        "Retrouvez un texte dans les conversations auxquelles vous avez accès.",
        "Find text in conversations you have access to.",
        "maquette",
        "mockup",
    ),
}


def social_action(text, interface_language="fr"):
    """Answer standalone pleasantries; never swallow an accompanying work request."""
    value = re.sub(r"[^a-z0-9]+", " ", normalized(text)).strip()
    phrases = {
        "fr": {
            "greeting": {"bonjour", "salut", "bonsoir", "coucou", "bonjour assistant"},
            "wellbeing": {
                "ca va",
                "comment ca va",
                "comment vas tu",
                "bonjour ca va",
                "salut ca va",
            },
            "thanks": {"merci", "merci beaucoup", "super merci"},
            "goodbye": {"au revoir", "a bientot", "bonne journee", "bonne soiree"},
        },
        "en": {
            "greeting": {
                "hello",
                "hi",
                "hey",
                "hello there",
                "good morning",
                "good afternoon",
                "good evening",
                "hello assistant",
            },
            "wellbeing": {"how are you", "hello how are you", "hi how are you"},
            "thanks": {"thanks", "thank you", "thanks a lot", "thank you very much"},
            "goodbye": {"bye", "goodbye", "see you", "have a nice day"},
        },
    }
    replies = {
        "fr": {
            "greeting": "Bonjour ! Comment puis-je vous aider avec vos projets et vos tâches ?",
            "wellbeing": "Bonjour ! Je suis prêt à vous aider. Que souhaitez-vous faire dans Design Workflow ?",
            "thanks": "Avec plaisir ! N’hésitez pas si vous avez une autre question.",
            "goodbye": "À bientôt et bonne continuation sur vos projets !",
        },
        "en": {
            "greeting": "Hello! How can I help you with your projects and tasks?",
            "wellbeing": "Hello! I’m ready to help. What would you like to do in Design Workflow?",
            "thanks": "You’re welcome! Let me know if you have another question.",
            "goodbye": "See you soon, and good luck with your projects!",
        },
    }
    for language, intents in phrases.items():
        for intent, variants in intents.items():
            if value in variants:
                return {"tool": "clarify", "message": replies[language][intent]}
    if text.strip() in {"👋", "👋🏻", "👋🏼", "👋🏽", "👋🏾", "👋🏿"}:
        language = "en" if interface_language == "en" else "fr"
        return {"tool": "clarify", "message": replies[language]["greeting"]}
    return None


def suggestions(user, language="fr"):
    caps = capabilities(user)
    entries = [
        entry for entry in STARTERS if entry[2] != "knowledge" or "create" in caps
    ]
    entries += [REPORT_STARTER] if is_admin(user) else []
    return [entry[1 if language == "en" else 0] for entry in entries]


def shortcut_catalog(user, language="fr"):
    en = language == "en"
    names = {target: source for source, target in ALIASES.items()} if en else {}
    items = [
        {
            "command": names.get(command, command),
            "title": english if en else french,
            "help": MODULE_HELP[resource][int(en)],
            "example": names.get(command, command)
            + " "
            + MODULE_HELP[resource][2 + int(en)],
        }
        for command, resource, french, english in MODULES
    ]
    items.append(
        {
            "command": names.get("/aide", "/aide"),
            "title": "Help" if en else "Aide",
            "help": (
                "Show the available shortcuts and how to use them."
                if en
                else "Affichez les raccourcis disponibles et leur utilisation."
            ),
            "example": names.get("/aide", "/aide"),
        }
    )
    if is_admin(user):
        items.append(
            {
                "command": names.get("/bilan", "/bilan"),
                "title": "Working time" if en else "Temps de travail",
                "help": (
                    "View recorded working time. Add an exact project name to filter it."
                    if en
                    else "Consultez le temps de travail enregistré. Ajoutez le nom exact d’un projet pour le filtrer."
                ),
                "example": names.get("/bilan", "/bilan") + " Atlas",
            }
        )
    return items


def shortcut_action(text, executor, interface_language="fr"):
    user = executor.authorize()
    for french, english, tool, args in STARTERS + [REPORT_STARTER]:
        if normalized(text).rstrip(".?! ") in (
            normalized(french).rstrip(".?! "),
            normalized(english).rstrip(".?! "),
        ):
            if tool == "time_report" and not is_admin(user):
                raise ChatAIError("PERMISSION_DENIED")
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
        if not is_admin(user):
            raise ChatAIError("PERMISSION_DENIED")
        return {"tool": "time_report", "arguments": {"project_name": arg}}
    if command == "/aide":
        return {
            "tool": "clarify",
            "message": "\n\n".join(
                x["command"]
                + " — "
                + x["title"]
                + "\n"
                + x["help"]
                + "\n"
                + ("Example: " if language == "en" else "Exemple : ")
                + x["example"]
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
