"""Complete the user-facing Git history and publish the 1.4.0 notes.

Sources (backend/frontend): 8798838/9f3fe09, 26ef64e/3c95cfd,
fa33d14/4a68722/eff30b8, 224c331/62bbaa8, d4790a9,
f7bdda4, bb8c8b8/1237459/791f999, c83a086/84ddc5e/7942d9d,
4428f7b, 68b904c/9175dd1, 2eb572c/e81d11a, 03f983a/e7369b6,
6b8f6af/0424290, 441381e/98eea64, c420a8b/67dbd90.
Dates follow Git milestones; pre-1.0 numbering remains retrospective.
Keep administrator wording and publication choices. Do not announce the
new app version until the frontend has been deployed and checked.
"""

from django.db import migrations

# Paired lines keep the French and English histories equivalent.
ADDITIONS = {
    "2026-04-30": [
        (
            "Attribuez les tâches, fixez leurs priorités et leurs dates cibles, puis archivez les cartes terminées.",
            "Assign tasks, set priorities and target dates, then archive finished cards.",
        ),
        (
            "Répondez à un message, réagissez avec un emoji, transférez-le ou programmez un rappel.",
            "Reply to a message, react with an emoji, forward it or schedule a reminder.",
        ),
        (
            "Transformez un échange du chat en tâche pour retrouver le contexte de la demande.",
            "Turn a chat conversation into a task so the original request stays easy to find.",
        ),
        (
            "Envoyez des images, des fichiers et des messages vocaux dans les conversations.",
            "Send images, files and voice messages in conversations.",
        ),
        (
            "Mentionnez une personne avec @, liez un projet ou une tâche avec # et épinglez les messages importants.",
            "Mention someone with @, link a project or task with # and pin important messages.",
        ),
        (
            "Utilisez des modèles de listes pour préparer un brief, suivre la conception ou organiser une livraison.",
            "Use checklist templates to prepare a brief, follow design work or organize a delivery.",
        ),
    ],
    "2026-05-04": [
        (
            "Passez du tableau de cartes à la table ou au calendrier selon ce que vous souhaitez consulter.",
            "Switch between the card board, table and calendar to suit the information you need.",
        ),
        (
            "Recherchez les projets, les tâches, les fichiers et les messages depuis un même endroit.",
            "Search projects, tasks, files and messages in one place.",
        ),
        (
            "Conservez les différentes versions d’un fichier et ajoutez des annotations pour préciser les corrections attendues.",
            "Keep different file versions and add annotations to explain the changes needed.",
        ),
        (
            "Échangez dans la conversation générale, en privé ou dans les conversations liées aux projets et aux tâches.",
            "Use the general chat, private conversations or chats linked to projects and tasks.",
        ),
        (
            "Réglez vos préférences de notifications, marquez les alertes comme lues ou reportez un rappel.",
            "Set notification preferences, mark alerts as read or snooze a reminder.",
        ),
        (
            "Consultez l’historique d’une tâche pour retrouver les changements et leurs auteurs.",
            "Check a task's history to see what changed and who changed it.",
        ),
    ],
    "2026-09-14": [
        (
            "Les cartes gardent une fenêtre de taille stable et les actions restent mieux espacées.",
            "Card dialogs keep a stable size and their actions have clearer spacing.",
        ),
        (
            "Les photos de profil sont utilisées en priorité ; les initiales apparaissent lorsqu’aucune photo n’est disponible.",
            "Profile pictures appear first; initials are shown when no picture is available.",
        ),
    ],
    "2026-09-15": [
        (
            "Le temps de travail respecte les horaires : 9 h–13 h et 14 h–18 h en semaine, 9 h–13 h le samedi, dimanche non compté.",
            "Working time follows office hours: 9 am–1 pm and 2 pm–6 pm on weekdays, 9 am–1 pm on Saturday, with Sundays excluded.",
        ),
        (
            "Retrouvez toutes les cartes après avoir quitté une vue enregistrée et rétabli la vue par défaut.",
            "See all cards again after leaving a saved view and returning to the default view.",
        ),
        (
            "Cliquez sur toute la ligne d’un élément de liste pour le cocher ou le décocher.",
            "Click anywhere on a checklist item row to check or uncheck it.",
        ),
        (
            "Le chat montre les personnes ayant réagi à un message, avec votre propre réaction clairement identifiée.",
            "Chat shows who reacted to a message and clearly identifies your own reaction.",
        ),
        (
            "Les champs de création de tâche et de rappel ne contiennent plus de texte à effacer avant de commencer.",
            "New task and reminder fields no longer contain prefilled text that you need to delete.",
        ),
    ],
    "2026-09-16": [
        (
            "Les designers peuvent retirer les images et les pièces jointes des cartes sur lesquelles ils travaillent.",
            "Designers can remove images and attachments from cards they work on.",
        ),
        (
            "Ajoutez un nom explicite aux images et aux fichiers pour expliquer leur contenu.",
            "Give images and files a meaningful name to describe their content.",
        ),
        (
            "Les outils de carte restent ouverts pendant votre travail ; refermez-les avec leur bouton ou la croix.",
            "Card tools stay open while you work; close them using their button or the cross.",
        ),
        (
            "Les actions de revue tiennent compte de votre rôle et de l’étape en cours. La validation demande une confirmation.",
            "Review actions match your role and the current stage. Approval requires confirmation.",
        ),
        (
            "La création d’une carte propose uniquement les projets sur lesquels vous pouvez travailler.",
            "Creating a card only offers projects you are allowed to work on.",
        ),
        (
            "Les conversations contenant de nouveaux messages s’ouvrent dans le chat et leurs compteurs sont visibles dans le menu.",
            "Conversation groups with new messages expand in chat, with unread counts visible in the menu.",
        ),
    ],
    "2026-10-03": [
        (
            "Les notifications, les menus et les formulaires sont plus lisibles en mode sombre.",
            "Notifications, menus and forms are easier to read in dark mode.",
        ),
        (
            "Les noms longs et les barres de filtres s’affichent plus clairement, sans texte coupé.",
            "Long names and filter bars are clearer, with fixes for clipped text.",
        ),
    ],
    "2026-10-05": [
        (
            "Les nouveaux projets et les nouvelles tâches sont proposés dans les références du chat sans recharger la page.",
            "New projects and tasks become available in chat references without reloading the page.",
        ),
        (
            "Les rappels et les changements de droits sont pris en compte pendant que l’application est ouverte.",
            "Reminders and access changes are reflected while the app is open.",
        ),
        (
            "Un avertissement protège votre saisie lorsqu’une autre personne modifie en même temps les informations d’une carte.",
            "A warning protects your draft when someone else edits the same card information.",
        ),
        (
            "Le temps partagé compte pour chaque participant : une heure à deux représente deux heures de travail au total.",
            "Shared time is counted for each participant: one hour with two people represents two hours of work in total.",
        ),
    ],
    "2026-10-06": [
        (
            "L’accueil met en avant les retards, les blocages et les projets qui demandent votre attention.",
            "The home page highlights overdue work, blockers and projects that need your attention.",
        ),
        (
            "Les rapports distinguent le temps prévu, le temps consacré et le travail terminé, selon la période et les filtres choisis.",
            "Reports distinguish planned time, time spent and completed work for the selected period and filters.",
        ),
        (
            "Une journée affichée dans les rapports représente 8 heures. Le samedi compte 4 heures et la pause déjeuner est exclue.",
            "One day in reports represents 8 hours. Saturday counts as 4 hours and the lunch break is excluded.",
        ),
        (
            "Les PDF reprennent le périmètre du rapport et expliquent les indicateurs ; les exports CSV et d’analyse ont été retirés.",
            "PDFs include the report scope and explain its indicators; CSV and analysis exports have been removed.",
        ),
        (
            "Consultez le détail du temps d’une tâche et ajoutez une durée manuelle si nécessaire.",
            "View a task's time entries and add a manual duration when needed.",
        ),
        (
            "Les messages s’affichent dès leur envoi, avec une indication si l’envoi doit être réessayé.",
            "Messages appear as soon as you send them, with an indication when sending needs to be retried.",
        ),
        (
            "Les notifications de l’ordinateur peuvent émettre un son et ouvrir la tâche ou la conversation concernée.",
            "Desktop notifications can play a sound and open the relevant task or conversation.",
        ),
    ],
    "2026-10-07": [
        (
            "Renommez une pièce jointe directement sur la carte ou dans la page de la tâche, sans devoir la renvoyer.",
            "Rename an attachment directly on the card or task page without uploading it again.",
        ),
        (
            "Les anciennes grandes images de couverture sont réduites en aperçus légers pour corriger les erreurs d’affichage. Les pièces jointes restent inchangées.",
            "Older large cover images are reduced to lightweight previews to fix display errors. Attachments stay unchanged.",
        ),
        (
            "L’historique des nouveautés est complété avec les fonctionnalités et améliorations des versions précédentes.",
            "The changelog now includes more features and improvements from earlier releases.",
        ),
    ],
}

NEW_ENTRIES = [
    (
        "2026-05-06",
        "0.2.1",
        "Votre espace adapté au mobile",
        "A workspace that fits your phone",
        [
            (
                "Les projets, les rapports, le chat et les notifications s’adaptent mieux aux petits écrans.",
                "Projects, reports, chat and notifications adapt better to small screens.",
            ),
            (
                "Ouvrez une tâche dans sa propre page pour consulter ses informations et ses outils.",
                "Open a task on its own page to access its details and tools.",
            ),
        ],
    ),
    (
        "2026-05-08",
        "0.2.2",
        "Des échanges et des cartes plus lisibles",
        "Clearer conversations and cards",
        [
            (
                "Les aperçus du chat présentent le contenu chargé et facilitent la lecture des échanges.",
                "Chat previews display loaded content and make conversations easier to read.",
            ),
            (
                "Les colonnes du tableau, les avatars et les fenêtres de cartes sont mieux alignés sur ordinateur et mobile.",
                "Board columns, avatars and card dialogs are better aligned on desktop and mobile.",
            ),
        ],
    ),
    (
        "2026-05-20",
        "0.3.1",
        "Une connexion plus cohérente",
        "More consistent sign-in",
        [
            (
                "La connexion centralisée retrouve correctement votre profil et vos droits.",
                "Central sign-in correctly loads your profile and access rights.",
            ),
            (
                "L’écran de connexion est mieux centré sur mobile et son arrière-plan est plus discret.",
                "The sign-in screen is better centered on mobile with a more subtle background.",
            ),
        ],
    ),
    (
        "2026-06-08",
        "0.3.2",
        "Des résultats de recherche plus justes",
        "More accurate search results",
        [
            (
                "La recherche de tâches respecte mieux les filtres sélectionnés.",
                "Task search follows the selected filters more accurately.",
            ),
        ],
    ),
    (
        "2026-09-07",
        "0.4.3",
        "Un envoi d’e-mails plus fiable",
        "More reliable email delivery",
        [
            (
                "Amélioration de la fiabilité des e-mails de compte et des notifications envoyées par e-mail.",
                "Improved reliability of account emails and email notifications.",
            ),
        ],
    ),
    (
        "2026-09-23",
        "1.0.1",
        "Une application mieux entretenue",
        "App maintenance improvements",
        [
            (
                "Mise à jour de l’application pour améliorer sa compatibilité et faciliter les prochaines évolutions.",
                "App updates to improve compatibility and support future improvements.",
            ),
        ],
    ),
]


def complete_release_notes(apps, schema_editor):
    entries = apps.get_model("ws", "ChangelogEntry").objects.using(
        schema_editor.connection.alias
    )
    for release_date, version, title_fr, title_en, changes in NEW_ENTRIES:
        entries.get_or_create(
            date=release_date,
            defaults={
                "version": version,
                "title_fr": title_fr,
                "title_en": title_en,
                "changes_fr": "\n".join(pair[0] for pair in changes),
                "changes_en": "\n".join(pair[1] for pair in changes),
                "is_published": True,
            },
        )

    for release_date, changes in ADDITIONS.items():
        saved = entries.filter(date=release_date).first()
        if saved is None:
            continue
        for index, language in enumerate(("fr", "en")):
            field = f"changes_{language}"
            text = getattr(saved, field)
            existing = {line.strip() for line in text.splitlines()}
            missing = [pair[index] for pair in changes if pair[index] not in existing]
            if missing:
                # Append, preserving all administrator-edited wording and spacing.
                setattr(
                    saved,
                    field,
                    text
                    + ("\n" if text and not text.endswith("\n") else "")
                    + "\n".join(missing),
                )
        fields = ["changes_fr", "changes_en"]
        if release_date == "2026-10-07" and saved.version in ("", "1.3.1"):
            saved.version = "1.4.0"
            fields.append("version")
            titles = {
                "fr": (
                    "Un historique des nouveautés plus lisible",
                    "Des fichiers mieux identifiés, des cartes plus légères",
                ),
                "en": (
                    "An easier-to-read release history",
                    "Clearer file names, lighter cards",
                ),
            }
            for language, (previous, current) in titles.items():
                field = f"title_{language}"
                if getattr(saved, field) == previous:
                    setattr(saved, field, current)
                    fields.append(field)
        saved.save(update_fields=fields)


class Migration(migrations.Migration):
    dependencies = [("ws", "0007_correct_release_history")]
    operations = [
        migrations.RunPython(complete_release_notes, migrations.RunPython.noop)
    ]
