FIELD_LABELS = {
    "start_date": "Date de début",
    "target_end_date": "Date cible",
    "due_date": "Date cible",
    "blocked_reason": "Motif du blocage",
    "in_progress": "En cours",
    "in_review": "En revue",
    "on_hold": "En pause",
    "current_assignee": "Responsable",
    "record_id": "Élément",
}


def selected_action_text(operation, resource, language="fr"):
    if language == "en":
        return f"{'Archive' if operation == 'archive' else 'Edit'} · {'Project' if resource == 'project' else 'Task'}"
    return f"{'Archiver' if operation == 'archive' else 'Modifier'} · {'Projet' if resource == 'project' else 'Tâche'}"
