---
name: linear-card
description: Décide si un problème vérifié mérite une carte Linear, un commentaire sur un ticket existant ou une entrée du registre des défauts connus. Use before opening a Linear card, before proposing "je crée un ticket ?", or when an investigation ends on a verified defect.
---

# Carte Linear ou registre des défauts connus

Un problème vérifié ne devient pas automatiquement une carte. Il faut trois oui :

1. Y a-t-il une victime identifiable (un client, un chiffre faux, une perte de données) ?
2. Est-ce que ça revient ?
3. Quelqu'un va-t-il vraiment la prendre ce trimestre ?

Sinon, l'entrée va dans le registre `docs/debugging/defauts-connus.md` du dépôt, pas dans le
backlog de quelqu'un : ce que la personne voit, la cause avec sa ligne de preuve, quoi
répondre, et le contournement s'il existe.

- Une carte qui regroupe une famille vaut mieux que quatre qui la découpent.
- Un commentaire sur le ticket existant vaut mieux qu'une nouvelle carte dès que le sujet a
  déjà un propriétaire : chercher d'abord (`list_issues` avec les mots-clés).
- Tout chiffre de la carte porte la requête ou le chemin du script qui l'a produit.
- Créer ou commenter est une écriture visible par l'équipe : montrer le brouillon, attendre le go.
- Une carte créée sort complète : l'utilisateur en assignee, une priorité (Medium par défaut), un label de
  l'équipe, puis le customer existant lié par `save_customer_need`. `guard-linear-issue.py`
  bloque la création sans les trois premiers ; le skill `linear-complete` rattrape l'existant.
