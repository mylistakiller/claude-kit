---
name: linear-complete
description: Complète les cartes Linear ouvertes de l'utilisateur (celles dont il est l'assignee) auxquelles il manque la priorité, un label ou le customer, via le MCP Linear, avec un plan montré avant toute écriture. Use when the user asks to "compléter mes cartes Linear", "mettre les priorités et labels manquants", "lier les customers aux cartes", "linear-complete", or when the SessionStart hook signals the weekly pass is due. Pour une carte que l'agent crée, c'est `guard-linear-issue.py` qui l'impose au moment de la création.
allowed-tools: Bash, AskUserQuestion
---

# Compléter ses cartes Linear

Règle : une carte porte l'utilisateur en assignee, une priorité (Medium quand
personne n'en a donné), au moins un label, et le customer quand il existe déjà dans Linear.
À la création par l'agent, `hooks/guard-linear-issue.py` bloque une carte incomplète. Cette
passe rattrape les cartes déjà ouvertes, créées par d'autres ou avant la règle.

Pas de clé API Linear sur la machine : tout passe par le MCP `plugin_engineering_linear`.

## Déroulé

1. **Périmètre.** `list_issues` avec `assignee: "me"`, un appel par état ouvert (`triage`,
   `backlog`, `unstarted`, `started`), `fields: [id, title, description, priority, labels,
   team, statusType]`, en suivant `cursor` jusqu'au bout. Une carte dont l'utilisateur
   n'est pas l'assignee n'est jamais touchée, même si elle apparaît ailleurs.

2. **Ce qui manque, carte par carte.**
   - Priorité : `priority.value == 0` devient 3 (Medium). Une priorité existante ne bouge pas.
   - Label : aucun label. Choisir **un** label de l'équipe de la carte (`list_issue_labels`
     avec `team`) d'après le titre et la description, par exemple « Bug » pour un dysfonctionnement ou
     « Feature » pour une demande. Sans label évident, proposer le label fourre-tout de
     l'équipe s'il existe, ou le signaler sans rien poser.
   - Customer : le client cité dans le titre ou la description. `list_customers` avec
     `query` sur ce nom et `includeNeeds: true`. Lier seulement quand **un seul** customer
     existant correspond et qu'aucun de ses `needs` ne pointe déjà cette carte. Jamais de
     création de customer : sans correspondance ou avec plusieurs, l'écrire dans le plan.

3. **Plan à blanc.** Montrer une ligne par carte touchée : `<ticket-id> | priorité 0 → 3 |
   label + Bug | customer <client> (existant)`, puis les cartes écartées et
   pourquoi (customer absent, ambigu, label incertain). Les comptes : cartes lues, cartes à
   compléter, écritures prévues par type.

4. **Go de l'utilisateur.** Un `AskUserQuestion` : `Appliquer tout` / `Appliquer sans les labels` /
   `Choisir carte par carte` / `Ne rien faire`. Ces écritures sont visibles par l'équipe.

5. **Appliquer.** `save_issue` avec `id`, `priority` et `addLabels` (jamais `labels`, qui
   remplace l'ensemble) ; `save_customer_need` avec `issue` et `customer`.

6. **Vérifier.** `get_issue` sur chaque carte écrite (priorité et labels relus) et
   `list_customers` avec `includeNeeds` pour chaque lien posé. Rapport : écrit, vérifié,
   écarté. Puis `${CLAUDE_PLUGIN_ROOT}/scripts/linear-complete.py done`.

## Garde-fous

- `addLabels` uniquement : `labels` efface les labels posés par l'équipe.
- Une carte en `completed` ou `canceled` ne fait pas partie de la passe.
- Ne pas changer l'assignee d'une carte existante : la passe ne lit que celles de l'utilisateur.
