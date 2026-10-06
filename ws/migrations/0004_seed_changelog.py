"""Curated user-facing milestones from both repositories, not raw commit text.

Sources (frontend/backend): 0424290/6b8f6af, e81d11a/2eb572c,
9175dd1/68b904c, 7942d9d/d22ce7e, 791f999/7d50470, ab1b81d,
3f999a0/fa84df4, a72a460/ca4ea3d, 3ed46e2, 642674d/45cf4cc,
3c95cfd/26ef64e, 9f3fe09/8798838. Dates are Git change dates, not
claims about the exact time a deployment reached production.
"""

from django.db import migrations

ENTRIES = [
    (
        "2026-10-06",
        "Un suivi plus clair pour toute l’équipe",
        "A clearer picture of the team's work",
        [
            "L’accueil montre les tâches créées et terminées chaque jour pour mieux suivre l’avancement.",
            "Les rapports présentent le temps en jours et en heures de travail, avec des explications plus faciles à comprendre.",
            "Les avatars des collaborateurs apparaissent sur les cartes, à côté du responsable.",
            "Corrections de bugs et amélioration de la fiabilité de l’application.",
        ],
        [
            "The home page shows tasks created and completed each day to make progress easier to follow.",
            "Reports show time in working days and hours, with clearer explanations.",
            "Collaborators' avatars appear on cards alongside the task owner.",
            "Bug fixes and improved app reliability.",
        ],
    ),
    (
        "2026-10-05",
        "Travailler ensemble, en direct",
        "Work together, live",
        [
            "Les changements sur les cartes, les projets et le chat sont visibles sans actualiser la page.",
            "Les collaborateurs d’un projet peuvent modifier et déplacer ses cartes. Le temps de travail est suivi pour chaque participant.",
            "Ajoutez plusieurs pièces jointes à la fois. Le nom de chaque fichier est proposé automatiquement et reste modifiable.",
            "Renommez une carte en double-cliquant sur son titre lorsqu’elle est ouverte.",
            "Choisissez clairement le projet de destination lorsque vous créez une carte depuis le tableau.",
            "Amélioration de la reprise après une coupure de connexion et de la protection des modifications en cours.",
        ],
        [
            "Changes to cards, projects and chat appear without refreshing the page.",
            "Project collaborators can edit and move its cards. Working time is tracked for each participant.",
            "Attach several files at once. Each filename is suggested automatically and can still be edited.",
            "Rename an open card by double-clicking its title.",
            "Choose the destination project clearly when creating a card from the board.",
            "Improved recovery after connection interruptions and protection of edits in progress.",
        ],
    ),
    (
        "2026-10-03",
        "Mode sombre et projets partagés",
        "Dark mode and shared projects",
        [
            "Passez du mode clair au mode sombre selon votre préférence.",
            "Ajoutez plusieurs collaborateurs à un projet depuis ses informations.",
            "Survolez un avatar sur une carte pour voir le nom de la personne.",
            "Joignez des fichiers volumineux, jusqu’à 10 Go par fichier, avec un suivi de l’envoi.",
            "Amélioration de l’affichage et correction des erreurs lors de l’ajout d’images.",
        ],
        [
            "Switch between light and dark mode to suit your preference.",
            "Add several collaborators from a project's details.",
            "Hover over an avatar on a card to see the person's name.",
            "Attach large files, up to 10 GB each, and follow their upload progress.",
            "Display improvements and fixes for image uploads.",
        ],
    ),
    (
        "2026-09-16",
        "Des tâches et des validations plus simples",
        "Simpler tasks and reviews",
        [
            "Retrouvez votre travail dans le tableau de tâches et utilisez les filtres pour choisir vos projets.",
            "Consultez les autres projets et leurs tâches en lecture seule lorsque vous n’y participez pas.",
            "Confirmez l’envoi en revue : la carte passe dans « En revue ». Après validation du manager, elle passe dans « Terminé ».",
            "Mentionnez une personne avec @ dans les descriptions et les commentaires.",
            "Repérez les messages non lus grâce aux compteurs du chat et de ses conversations.",
            "Le chat indique quand une personne écrit. Les calendriers suivent la langue choisie.",
            "Corrections du déplacement des cartes et amélioration de l’utilisation sur mobile.",
        ],
        [
            "Find your work on the task board and use filters to choose your projects.",
            "View other projects and their tasks in read-only mode when you are not a participant.",
            "Confirm a review request to move the card to In review. A manager's approval moves it to Done.",
            "Mention someone with @ in descriptions and comments.",
            "Spot unread messages using the counters for chat and its conversations.",
            "Chat shows when someone is typing. Calendars follow your selected language.",
            "Fixes for moving cards and improvements to mobile usability.",
        ],
    ),
    (
        "2026-09-15",
        "Mieux organiser ses projets",
        "Organize your projects more easily",
        [
            "Archivez un projet avec ses tâches. Réactivez d’abord le projet avant de restaurer ses cartes.",
            "Créez vos projets et vos tâches, puis modifiez les informations d’une tâche directement depuis le projet.",
            "Créez, modifiez et retirez vos étiquettes personnelles pour organiser vos cartes.",
            "Filtrez les rapports par projet et par personne.",
            "Amélioration des rappels, des choix de dates et de la création de tâches depuis le chat.",
        ],
        [
            "Archive a project together with its tasks. Reactivate the project before restoring its cards.",
            "Create your projects and tasks, then edit a task's details directly from its project.",
            "Create, edit and remove your personal labels to organize cards.",
            "Filter reports by project and by person.",
            "Improvements to reminders, date selection and task creation from chat.",
        ],
    ),
    (
        "2026-09-14",
        "Une interface plus claire au quotidien",
        "A clearer everyday interface",
        [
            "Les boutons, les statuts et les étiquettes utilisent des couleurs plus faciles à distinguer.",
            "Les fenêtres de cartes et les formulaires sont plus lisibles et mieux alignés.",
            "L’écran de connexion reprend l’apparence du tableau de tâches.",
        ],
        [
            "Buttons, statuses and labels use colors that are easier to distinguish.",
            "Card windows and forms are easier to read and better aligned.",
            "The sign-in screen reflects the task board's appearance.",
        ],
    ),
    (
        "2026-07-29",
        "Un chat plus fiable",
        "More reliable chat",
        [
            "Les comptes désactivés ne sont plus proposés lors du transfert d’un message.",
            "Corrections de bugs dans la conversation générale et amélioration de la connexion en direct.",
        ],
        [
            "Deactivated accounts are no longer offered when forwarding a message.",
            "Bug fixes in the general conversation and improved live connectivity.",
        ],
    ),
    (
        "2026-07-01",
        "Des cartes plus faciles à utiliser",
        "Easier-to-use cards",
        [
            "Corrections de l’ouverture des outils de carte et de la suppression des listes.",
            "Amélioration de l’espacement et de la lisibilité dans les projets et le chat.",
        ],
        [
            "Fixes for opening card tools and deleting checklists.",
            "Improved spacing and readability in projects and chat.",
        ],
    ),
    (
        "2026-06-09",
        "Une navigation plus agréable sur mobile",
        "Smoother mobile navigation",
        [
            "Le menu latéral s’adapte mieux aux petits écrans.",
            "Les notifications restent dans l’écran et la barre du haut reste accessible pendant le défilement.",
        ],
        [
            "The side menu adapts better to smaller screens.",
            "Notifications fit within the screen and the top bar stays accessible while scrolling.",
        ],
    ),
    (
        "2026-05-13",
        "Une connexion simplifiée",
        "Simpler sign-in",
        [
            "Accédez à l’application avec la connexion centralisée de l’entreprise.",
            "Le choix entre le français et l’anglais est plus visible.",
        ],
        [
            "Access the app using the company's central sign-in.",
            "The choice between French and English is easier to find.",
        ],
    ),
    (
        "2026-05-04",
        "Un espace complet pour suivre le travail",
        "A complete workspace for your team",
        [
            "Enregistrez vos vues du tableau pour retrouver rapidement vos filtres.",
            "Soumettez les tâches en revue et suivez les demandes de modifications et les validations.",
            "Suivez l’activité, les notifications et la répartition du travail dans l’équipe.",
        ],
        [
            "Save board views to quickly return to your filters.",
            "Submit tasks for review and follow change requests and approvals.",
            "Follow activity, notifications and the team's workload.",
        ],
    ),
    (
        "2026-04-30",
        "Vos projets prennent place sur le tableau",
        "Your projects come to life on the board",
        [
            "Organisez les tâches sous forme de cartes et déplacez-les entre les étapes du travail.",
            "Complétez les cartes avec des commentaires, des listes, des étiquettes et des fichiers.",
            "Échangez avec l’équipe dans le chat de l’application.",
        ],
        [
            "Organize tasks as cards and move them between stages of work.",
            "Add comments, checklists, labels and files to your cards.",
            "Talk to the team in the app's chat.",
        ],
    ),
]


def seed_changelog(apps, schema_editor):
    entry = apps.get_model("ws", "ChangelogEntry")
    for date, title_fr, title_en, changes_fr, changes_en in ENTRIES:
        entry.objects.using(schema_editor.connection.alias).get_or_create(
            date=date,
            defaults={
                "title_fr": title_fr,
                "title_en": title_en,
                "changes_fr": "\n".join(changes_fr),
                "changes_en": "\n".join(changes_en),
                "is_published": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("ws", "0003_changelog_entry")]
    # Keep any subsequently edited notes if this data migration is reversed.
    operations = [migrations.RunPython(seed_changelog, migrations.RunPython.noop)]
