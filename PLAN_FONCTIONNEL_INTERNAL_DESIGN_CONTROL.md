# Plan fonctionnel - Internal Design Control

## 1. Vision generale

Internal Design Control est une application interne de pilotage du travail de l'equipe design.
Son objectif est de donner au responsable une vision simple, immediate et fiable de ce qui est en cours, de ce qui est bloque, de qui travaille sur quoi, du temps passe, et des priorites a venir.

L'idee generale se rapproche de Trello dans l'esprit visuel et dans la simplicite de suivi, mais avec un pilotage plus adapte a une equipe interne et a des besoins de management:

- voir les projets en cours
- voir les taches rattachees a chaque projet
- voir a quel collaborateur chaque tache est assignee
- voir le temps passe par personne et par projet
- reassigner une tache d'un membre a un autre
- suivre les priorites, les dates limites et les blocages
- garder un historique clair de toutes les actions importantes

L'application doit devenir un outil de pilotage quotidien pour le responsable, et un outil d'organisation simple pour l'equipe.

## 2. Objectifs metier

Cette application doit repondre a cinq besoins principaux.

### A. Savoir sur quoi travaille l'equipe en temps reel
Le responsable doit pouvoir ouvrir l'application et comprendre en quelques secondes:

- quels projets sont actifs
- quelles taches sont en cours
- quelles taches sont en retard
- quelles taches sont bloquees
- quels collaborateurs ont trop de charge
- quels collaborateurs sont disponibles pour reprendre du travail

### B. Mieux repartir la charge de travail
L'application doit aider a reequilibrer le travail dans l'equipe.
Le responsable doit pouvoir voir rapidement si une personne a trop de taches ou trop d'heures prevues, puis reassigner une partie du travail a un autre membre.

### C. Suivre le temps passe de maniere claire
L'outil doit permettre de savoir:

- combien de temps a ete passe sur une tache
- combien de temps a ete passe sur un projet
- combien de temps chaque collaborateur a consacre sur une periode donnee
- si le temps reel depasse ou non le temps estime

### D. Donner plus de visibilite sans complexifier le travail de l'equipe
Les designers ne doivent pas avoir un outil lourd.
Ils doivent surtout pouvoir:

- voir ce qui existe
- voir ce qui leur est assigne
- mettre a jour leur progression
- signaler un blocage
- laisser un commentaire
- enregistrer le temps passe

### E. Garder une trace des decisions et changements
Quand une tache change de statut, change de priorite, change de date, ou change de responsable, cela doit rester visible dans l'historique.
C'est essentiel pour eviter les pertes d'information et pour comprendre ce qui s'est passe sur un projet.

## 3. Principe de fonctionnement

Pour que l'application soit claire, il faut distinguer deux niveaux:

### Projet
Le projet represente l'initiative globale.
Exemples:

- amenagement showroom
- refonte d'un espace interne
- creation d'un concept de presentation
- suivi design d'un chantier interne

### Tache
La tache represente l'unite de travail concrete qui avance dans le tableau.
Exemples:

- preparer le moodboard
- valider le plan 3D
- revoir les materiaux
- corriger la presentation client
- mettre a jour les visuels

Cette distinction est importante.
Le projet donne une vue globale.
La tache donne la vraie vision operationnelle du quotidien.
C'est donc la tache qui doit etre assignee, deplacee, chronometree, commentee et reattribuee.

## 4. Utilisateurs et droits

Au lancement, il est recommande de garder un modele simple avec deux profils.

### Manager
Le manager peut:

- voir tous les projets et toutes les taches
- creer des projets et des taches
- assigner et reassigner le travail
- changer les priorites et les echeances
- consulter les rapports et les tableaux de bord
- voir la charge de travail complete de toute l'equipe
- suivre les retards, blocages et depassements

### Designer
Le designer peut:

- voir le tableau global en lecture seule pour comprendre l'activite de l'equipe
- modifier uniquement les taches qui lui sont attribuees
- changer l'etat d'avancement de ses taches
- enregistrer son temps passe
- ajouter des commentaires
- signaler un blocage ou un besoin de revision

Ce choix donne de la transparence a l'equipe, tout en gardant le controle de pilotage au manager.

## 5. Fonctionnalites principales

## 5.1 Tableau de bord manager

C'est l'ecran principal pour le responsable.
Il doit permettre de voir immediatement:

- nombre de projets actifs
- nombre de taches en attente, en cours, en revision, bloquees et terminees
- taches en retard
- collaborateurs les plus charges
- collaborateurs ayant encore de la capacite
- heures passees cette semaine
- ecart entre temps estime et temps reel
- nombre de reassignations recentes

L'objectif n'est pas seulement de lister des informations, mais de faire ressortir les points qui demandent une decision rapide.

## 5.2 Vue tableau de type Kanban

C'est la vue la plus importante pour le suivi quotidien.
Les taches sont presentees par colonnes selon leur etat.

Statuts recommandes:

- Backlog
- A faire
- En cours
- En revision
- Bloque
- Termine

Chaque carte doit afficher de facon simple:

- le nom de la tache
- le projet auquel elle appartient
- la personne responsable
- le niveau de priorite
- la date limite
- le temps estime
- le temps deja passe
- un indicateur visuel si la tache est en retard ou bloquee

Cette vue doit permettre au manager de comprendre instantanement la situation et de deplacer les priorites si necessaire.

## 5.3 Gestion des projets

Chaque projet doit avoir une fiche claire contenant:

- le nom du projet
- une description
- la personne ou l'equipe responsable
- la date de debut
- la date cible de fin
- le niveau de priorite globale
- l'etat general du projet
- la liste des taches du projet
- le temps total passe sur le projet
- les commentaires et l'historique principal

Le projet sert surtout a regrouper et lire l'avancement general.
Le vrai detail operationnel se fait au niveau des taches.

## 5.4 Gestion des taches

Chaque tache doit pouvoir contenir:

- un titre
- une description
- un projet parent
- un responsable actuel
- une priorite
- une date limite
- une estimation du temps necessaire
- le temps reel cumule
- un statut
- des commentaires
- un historique d'actions

Le responsable doit aussi pouvoir:

- reassigner une tache
- changer la priorite
- repousser ou avancer la date limite
- voir qui a deja travaille dessus
- voir si elle est bloquee ou en attente de validation

## 5.5 Suivi du temps

Le suivi du temps est une fonction centrale de l'application.
Il ne doit pas etre lourd, mais il doit etre fiable.

Le systeme doit permettre:

- a chaque designer d'enregistrer le temps passe sur ses taches
- de cumuler automatiquement ce temps au niveau de la tache
- de remonter ce cumul au niveau du projet
- d'afficher le temps passe par collaborateur
- de comparer le temps estime au temps reel

Il est recommande de suivre deux indicateurs en parallele:

- le nombre de taches en charge
- le volume d'heures estimees et realisees

Pourquoi les deux?
Parce qu'une personne peut avoir peu de taches, mais tres lourdes, ou beaucoup de petites taches.
Les deux lectures sont utiles pour manager correctement la charge.

## 5.6 Reassignation du travail

La reassignation est une fonctionnalite cle.
Elle doit etre simple, rapide et parfaitement tracee.

Quand une tache est transferee d'un membre a un autre, le systeme doit:

- changer le responsable actuel
- conserver l'historique des personnes qui ont travaille dessus
- conserver les temps deja saisis par les anciens responsables
- memoriser qu'une reassignation a eu lieu
- permettre au manager de comprendre pourquoi le changement a ete fait

Cela evite de perdre l'historique reel du travail.
Une tache peut donc etre actuellement chez une personne, tout en gardant la trace du temps deja passe par une autre.

## 5.7 Charge de travail par membre

Le manager doit disposer d'une vue equipe qui montre clairement:

- le nombre de taches ouvertes par personne
- le nombre de taches en retard par personne
- le volume d'heures restantes par personne
- le volume d'heures deja passees sur la semaine ou le mois
- les personnes surchargees
- les personnes disponibles

Cette vue est importante pour prendre des decisions concretes de repartition.

## 5.8 Commentaires et communication contextuelle

Chaque tache doit permettre d'ajouter des commentaires.
L'objectif est de centraliser les remarques dans le contexte du travail, au lieu de disperser les informations dans WhatsApp, email ou oral.

Les commentaires permettent par exemple:

- de demander une correction
- d'expliquer un blocage
- de laisser une instruction
- de justifier un retard
- de clarifier une attente

Cela cree une memoire du projet beaucoup plus utile dans le temps.

## 5.9 Historique et journal d'activite

Chaque action importante doit pouvoir etre relue:

- creation d'une tache
- changement de statut
- changement de priorite
- changement de date limite
- reassignation
- ajout d'un commentaire
- ajout de temps passe

Ce journal est tres important pour un responsable.
Il permet de comprendre l'evolution reelle du travail sans dependre uniquement de la memoire des personnes.

## 5.10 Notifications

L'application doit aussi alerter les utilisateurs sur les evenements utiles.
Par exemple:

- une tache m'a ete assignee
- une tache approche de sa date limite
- une tache est en retard
- une tache m'est renvoyee en correction
- une tache a change de statut
- un commentaire a ete ajoute sur une tache importante

Les notifications doivent aider, pas polluer.
Elles doivent donc rester simples et pertinentes.

## 6. Vues et ecrans recommandes

Pour rester simple au depart, les vues suivantes sont recommandees.

### 1. Tableau de bord manager
Pour piloter l'ensemble.

### 2. Tableau Kanban
Pour suivre l'avancement quotidien des taches.

### 3. Liste des projets
Pour voir tous les projets et entrer dans leur detail.

### 4. Fiche projet
Pour voir les taches, le temps cumule, les priorites et l'historique principal d'un projet.

### 5. Fiche tache
Pour modifier l'etat, commenter, enregistrer le temps, voir l'historique et reassigner.

### 6. Vue equipe / charge de travail
Pour voir qui est surcharge, qui est disponible, et mieux redistribuer le travail.

### 7. Vue temps et rapports
Pour lire les heures par collaborateur, par projet et par periode.

### 8. Parametres et preferences de notifications
Pour regler les alertes utiles.

## 7. Ce que l'application doit absolument apporter des le lancement

Le lancement doit se concentrer sur la valeur immediate.
La premiere version doit donc absolument permettre:

- de creer des projets
- de creer des taches dans ces projets
- d'assigner une tache a un collaborateur
- de voir les taches dans un tableau visuel
- de faire avancer les taches par statut
- d'ajouter du temps passe
- de commenter une tache
- de reassigner une tache sans perdre l'historique
- de voir la charge de travail de l'equipe
- de voir les retards et blocages
- de consulter un tableau de bord manager simple mais utile

Si ces points sont bien faits, l'application aura deja une vraie utilite metier.

## 8. Ce qui peut attendre une deuxieme phase

Certaines idees sont interessantes, mais ne sont pas indispensables pour le lancement.
Il vaut mieux les garder pour une deuxieme phase.

A reporter apres validation du besoin reel:

- vue calendrier ou timeline avancee
- automatisations complexes
- gestion avancee des versions de revision
- systeme de discussion tres pousse
- pieces jointes evoluees
- planification de capacite tres fine

Le risque sinon est de construire un outil trop complexe trop tot.

## 9. Benefices attendus pour l'entreprise

Si l'application est bien mise en place, les gains attendus sont clairs.

### Meilleure visibilite manageriale
Le responsable sait enfin, sans devoir demander a chacun:

- ou en est chaque projet
- qui travaille sur quoi
- ou se situent les retards
- ou se trouvent les blocages
- qui peut reprendre de nouvelles taches

### Meilleure organisation de l'equipe
Le travail est mieux reparti, les oublis diminuent, les priorites sont plus visibles et les transferts sont plus propres.

### Meilleure maitrise du temps
L'entreprise comprend mieux combien de temps prennent les sujets, quels projets consomment le plus d'effort, et ou se trouvent les depassements.

### Meilleure tracabilite
Les decisions et changements importants restent visibles, ce qui reduit les pertes d'information et les incomprehensions.

### Meilleure capacite de pilotage
Le manager ne pilote plus au ressenti uniquement. Il pilote avec des faits simples, visuels et comprenables.

## 10. Exemple d'usage concret

Voici comment l'application serait utilisee dans la vraie vie.

1. Le manager cree un projet interne.
2. Il cree plusieurs taches rattachees a ce projet.
3. Il assigne chaque tache a un designer.
4. Les designers voient le tableau, comprennent les priorites et mettent a jour leur avancement.
5. Chacun enregistre le temps passe sur ses taches.
6. Si une personne est surchargee, le manager reattribue certaines taches.
7. Le systeme garde l'historique du temps deja passe et des changements.
8. Le manager consulte le tableau de bord pour voir l'etat general, les retards, les blocages et la charge de travail.
9. Le projet avance avec une vision plus claire, plus juste et plus facile a piloter.

## 11. Recommandation finale

La meilleure approche pour Internal Design Control est de construire un outil simple, clair et tres utile au quotidien, plutot qu'un systeme trop ambitieux des le depart.

La priorite doit etre:

- la visibilite manageriale
- la clarte de l'avancement
- la reassignation facile
- le suivi du temps
- la tracabilite
- la lecture de la charge de travail

En resume, l'application doit permettre au responsable de repondre tres vite a ces questions:

- Sur quoi travaille mon equipe en ce moment?
- Qu'est-ce qui avance bien?
- Qu'est-ce qui est bloque ou en retard?
- Qui est surcharge?
- Qui peut reprendre du travail?
- Combien de temps avons-nous deja passe?
- Quelles decisions de repartition dois-je prendre aujourd'hui?

Si l'outil repond bien a ces questions, il remplira parfaitement son role.

## 12. Proposition de perimetre initial valide

Pour la premiere version, je recommande de valider officiellement ce perimetre:

- gestion des projets
- gestion des taches
- tableau Kanban
- priorites
- dates limites
- suivi du temps
- commentaires
- reassignation
- historique des actions
- notifications utiles
- tableau de bord manager
- vue charge de travail equipe
- rapports simples sur le temps et l'avancement

C'est un perimetre solide, utile, realiste, et directement exploitable par le management.
