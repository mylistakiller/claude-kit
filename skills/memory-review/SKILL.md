---
name: memory-review
description: Revue quotidienne, cas par cas, du store mémoire d'un projet : décider fichier par fichier ce qui reste vrai, ce qui doit être réduit et ce qui doit mourir. Use when the user asks to "faire la revue mémoire", "nettoyer les mémoires", "trier la mémoire", "review my memories", "qu'est-ce qui est encore pertinent en mémoire", or when the SessionStart hook signals a memory review is due. Never deletes in bulk and never decides alone : un QCM par mémoire.
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion
---

# Revue mémoire quotidienne (cas par cas)

222 mémoires, 84 portant un marqueur de péremption (`PÉRIMÉ`, `NON APPLIQUÉ`, `RESTE`,
`non posté`, `à trancher`). Le recall les remonte comme si elles étaient vraies. Cette revue
en traite un petit lot par jour, **une décision par mémoire, jamais en masse**.

Moteur : `${CLAUDE_PLUGIN_ROOT}/scripts/memory-review.py`
(`candidates` / `show` / `keep` / `kill` / `stats` / `due`).
Store : `~/.claude/projects/<project-slug>/memory/`.

## Déroulé

1. **File d'attente** — `${CLAUDE_PLUGIN_ROOT}/scripts/memory-review.py candidates --n 8`.
   Le classement combine : jamais revue, marqueur de péremption, ancienneté du `modified`,
   mémoire `project` de plus de 60 j, orpheline de l'index (`MEMORY.md` + sous-index `_index-*.md`), liens `[[…]]` morts.
   Par défaut 8 par jour ; si l'utilisateur en demande plus ou moins, ajuster `--n`.

2. **Pour chaque candidate, dans l'ordre** :
   a. `memory-review.py show <name>` et lire en entier.
   b. **Vérifier avant de résumer**, pas après — c'est le point de la revue :
      - ticket cité → statut réel via le MCP Linear (`get_issue`) : Done/Canceled depuis
        longtemps ⇒ la partie opératoire de la mémoire est morte, le piège technique peut
        survivre ;
      - chemin, commande, colonne, flag cité → existe encore ? (`ls`, `rg`, `DESCRIBE`) ;
      - « écritures non appliquées », « commentaire non posté », `RESTE …` → est-ce fait
        depuis ? Un `git log`, une requête de comptage ou le ticket tranchent.
      Ne jamais présenter un doute comme un fait : si la vérification n'est pas concluante,
      le dire dans le résumé.
   c. Présenter à l'utilisateur **3 lignes maximum** : ce que la mémoire affirme / ce qui est vérifié
      encore vrai / ce qui est mort. Plus la recommandation, en un mot.
   d. **Un `AskUserQuestion` pour cette mémoire seule**, options :
      1. `Garder tel quel` — reste utile, rien à changer
      2. `Réduire / mettre à jour` — ne garder que le fait durable (le piège, la recette),
         retirer le narratif d'enquête et les compteurs datés, dates relatives → absolues
      3. `Supprimer` — le fait n'est plus vrai, ou ne l'est plus que pour un ticket clos
      4. `Arrêter la revue` — dernière option, obligatoire (règle QCM du CLAUDE.md)

3. **Appliquer la décision immédiatement, avant de passer à la suivante** :
   - Garder → `memory-review.py keep <name>`
   - Réduire → éditer le fichier (garder le frontmatter, `metadata.type` inchangé), puis
     `memory-review.py keep <name> --verdict updated --note "<ce qui a été coupé>"`
     Si la ligne d'index ne dit plus la vérité après la réduction (c'est le cas le plus
     fréquent : une description qui annonce un correctif « non commité » déjà déployé),
     la réécrire **par le script**, jamais à la main :
     `memory-review.py reindex <name> [--label "…"] [--hook "…"] [--dry-run]`.
     Sur une ligne qui porte plusieurs mémoires il ne touche que le segment visé, mais il
     exige le texte du crochet plutôt que de l'inventer.
   - Supprimer → `memory-review.py kill <name> --note "<pourquoi>"`
     (déplace dans `.trash/<date>.<fichier>`, retire sa ligne de `MEMORY.md` ou du sous-index qui la porte)

4. **Ne jamais faire disparaître une boucle ouverte avec la mémoire.** Si la mémoire
   supprimée ou réduite contenait un `RESTE`, un « non appliqué » ou un « non posté » encore
   valable, proposer à l'utilisateur de le sortir vers Linear (ticket ou commentaire) **avant** le kill,
   et l'énumérer dans le rapport final. Une suppression ne doit jamais être le moyen par
   lequel un travail en attente est oublié.

5. **Clôture** — `memory-review.py stats`, puis un rapport de 3-4 lignes : traitées,
   gardées / réduites / supprimées, boucles ouvertes extraites, et le reliquat à revoir.

## Retro hebdomadaire des sessions

Quand `${CLAUDE_PLUGIN_ROOT}/scripts/session-retro.py due` répond (une fois par semaine), la
faire **avant** la file des mémoires. Elle cherche ce qu'il faut changer dans
l'environnement de l'agent (hooks, skills, CLAUDE.md), pas dans le code. Idée reprise de
`/retro` de mattpocock/skills.

1. `session-retro.py signals --days 7` : blocages de hooks groupés par motif, corrections de
   l'utilisateur (messages qui ouvrent sur « non », « stop », « refais »...), sessions qui ont le plus
   lutté contre leurs outils.
2. Garder 3 à 5 candidats, les plus répétés d'abord. Lire le passage du transcript avant de
   conclure (`rg` du titre de session dans `~/.claude/projects/*/*.jsonl`) : une correction
   peut être un simple changement d'avis de l'utilisateur, pas une faute.
3. Classer chaque candidat :
   - **faute mécanique** (un motif fixe : commande, flag, table sans filtre, format) : un
     check déterministe, hook ou test de script, jamais une consigne de plus ;
   - **faute de jugement** : une ligne dans le skill que l'agent lit au moment d'agir, pas
     dans le CLAUDE.md global, qui ne reçoit que la méthode transverse ;
   - **blocage attendu** : le hook est la porte de confirmation voulue (`--apply`,
     `DRY=0`, `kubectl exec` d'écriture). Rien à changer, sauf si le même blocage revient
     *avant* le plan de `/prod-run` : l'agent a sauté le plan, c'est alors une faute de
     jugement ;
   - **outil coûteux ou info inaccessible** : un script `probe-*.py`, un accès lecture seule.
4. Un `AskUserQuestion` par candidat : `Appliquer la proposition` / `Noter sans changer` /
   `Ignorer` / `Arrêter la retro`. Un changement de hook ou de script se teste une fois,
   chemin d'échec compris, avant d'être annoncé.
5. `session-retro.py done`, puis enchaîner sur la file des mémoires.

## Garde-fous

- Une mémoire `type: feedback` ou `type: user` encode une consigne de travail de l'utilisateur : ne
  jamais la proposer à la suppression sans dire explicitement quelle consigne disparaîtrait.
- Ne pas supprimer un piège technique (« PIÈGE … ») au motif que le ticket est clos : le
  ticket meurt, le piège reste. Le réduire plutôt.
- Ne pas reformuler ce que l'utilisateur a déjà tranché : si une mémoire dit qu'un arbitrage a eu lieu,
  la revue ne le rouvre pas.
- Une seule mémoire par QCM. Pas de « et je supprime aussi les 4 suivantes ».
- `MEMORY.md` et les sous-index `_index-*.md` ne s'éditent qu'à travers le script, pour que
  l'index et les fichiers ne divergent jamais. `MEMORY.md` ne garde que la méthode transverse
  et un pointeur par thème : la ligne d'une mémoire va dans le sous-index de son thème.
