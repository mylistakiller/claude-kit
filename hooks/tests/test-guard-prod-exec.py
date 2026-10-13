#!/usr/bin/env python3
"""Suite de tests de guard-prod-exec.py.

    python3 hooks/tests/test-guard-prod-exec.py

Les 15 cas d'origine (2026-09-09, 7 qui passent et 8 qui bloquent) plus les cas de portee
ajoutes le 2026-09-10 : une ecriture de fichier local dont le CONTENU cite la commande de
prod ne doit pas bloquer, et un `kubectl` reellement invoque doit continuer de bloquer.

Cas ajoutes le 2026-09-18 : l'echappatoire `# allow-prod-write` exige desormais un verdict
`Relecteur:` propre (skill falsify) en derniere ligne de la session, pas seulement le mot-cle.

Cas ajoutes le 2026-09-19 : l'echappatoire exige EN PLUS un snapshot de rollback (Write ou
Bash produisant un `<ticket>-before-<date>.json`/`.csv`, /prod-run etape 2) dans la session.

Le nom de la commande est reconstitue par concatenation dans ce fichier, pour que le test
lui-meme ne soit pas bloque par le guard qu'il teste.
"""

import json
import subprocess
import sys
import os
from pathlib import Path
import tempfile

HOOK = str(Path(__file__).resolve().parents[1] / "guard-prod-exec.py")
# Le script exempte est configure par variable d'environnement, comme en vrai.
ENV = {**os.environ, "PROD_EXEC_ALLOWED_SCRIPTS": r"\.claude/scripts/safe-relaunch\.py\b"}
ENV.pop("PROD_EXEC_NONPROD_TARGETS", None)
K = "kube" + "ctl"  # ne pas ecrire le mot en clair, voir la docstring
E = "e" + "xec"

PASS, BLOCK = "passe", "bloque"
SNAPSHOTS = tempfile.TemporaryDirectory(prefix="guard-snapshots-")
SNAPSHOT_PATH = str(Path(SNAPSHOTS.name) / "eng-1234-before-20260919.json")
Path(SNAPSHOT_PATH).write_text('[{"id": 2}]')


def transcript_with(*items):
    """Fabrique un transcript JSONL minimal a partir d'items, dans l'ordre :
    - une chaine -> un tour assistant avec ce texte (les lignes `Relecteur: ...` du
      falsify guard vivent la, la derniere du transcript entier gagne) ;
    - ("write", chemin) -> un tour assistant qui appelle Write vers ce chemin ;
    - ("bash", commande) -> un tour assistant qui appelle Bash avec cette commande ;
    - ("result", texte) / ("error", texte) -> un tool_result (is_error pour le second) ;
    - ("ran", outil, sortie) / ("ran-error", outil, sortie) -> l'appel de cet outil puis son
      resultat, relies par tool_use_id (la ligne Relecteur n'est lue que dans un resultat Bash).
    """
    fh = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
    for n, item in enumerate(items):
        if isinstance(item, tuple) and item[0] in ("ran", "ran-error"):
            kind, tool, output = item
            tid = f"toolu_{n}"
            use = {"type": "tool_use", "id": tid, "name": tool, "input": {}}
            fh.write(json.dumps({"type": "assistant", "message": {"content": [use]}}) + "\n")
            res = {"type": "tool_result", "tool_use_id": tid, "content": output}
            if kind == "ran-error":
                res["is_error"] = True
            entry = {"type": "user", "message": {"role": "user", "content": [res]}}
            fh.write(json.dumps(entry) + "\n")
            continue
        if isinstance(item, tuple) and item[0] in ("result", "error"):
            kind, value = item
            block_ = {"type": "tool_result", "tool_use_id": "t", "content": value}
            if kind == "error":
                block_["is_error"] = True
            entry = {"type": "user", "message": {"role": "user", "content": [block_]}}
            fh.write(json.dumps(entry) + "\n")
            continue
        if isinstance(item, tuple):
            kind, value = item
            if kind == "write":
                content = [{"type": "tool_use", "name": "Write", "input": {"file_path": value}}]
            elif kind == "bash":
                content = [{"type": "tool_use", "name": "Bash", "input": {"command": value}}]
            elif kind == "read":
                content = [{"type": "tool_use", "name": "Read", "input": {"file_path": value}}]
            else:
                raise ValueError(kind)
        else:
            content = [{"type": "text", "text": item}]
        entry = {"type": "assistant", "message": {"content": content}}
        for block in content:
            if block.get("type") == "tool_use":
                block["id"] = f"toolu_{n}"
        fh.write(json.dumps(entry) + "\n")
        if isinstance(item, tuple) and item[0] in ("write", "bash"):
            fh.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {"content": [{"type": "tool_result", "tool_use_id": f"toolu_{n}", "content": "ok"}]},
                    }
                )
                + "\n"
            )
    fh.close()
    return fh.name


CLEAN_VERDICT = "Relecteur: n'a pas pu casser, 2 requêtes falsifiantes jointes."
SNAPSHOT_WRITE = ("write", SNAPSHOT_PATH)
CLEAN_INVARIANTS = "INVARIANTS:\nexpected_rows: 187\nforbidden[deleted_at IS NOT NULL]: 0\n"


def printed_snapshot(relative: bool, on_disk: bool):
    """2026-10-08: an apply script writes its own snapshot during the dry run and prints its
    path. The file lives in a fake harness scratchpad, /private/tmp/claude-test/<proj>/<sid>/,
    derived from the transcript path the same way the hook does it."""
    root = tempfile.mkdtemp()
    proj, sid = "proj-retro-test", "sid-" + os.path.basename(root)
    pad = f"/private/tmp/claude-test/{proj}/{sid}/scratchpad"
    os.makedirs(pad, exist_ok=True)
    name = "eng-1234-before-2026-10-08.json"
    if on_disk:
        open(os.path.join(pad, name), "w").write("[]")
    shown = name if relative else os.path.join(pad, name)
    src = transcript_with(
        ("ran", "Bash", f"dry run: 3 rows\nsnapshot: {shown}\n"),
        CLEAN_INVARIANTS,
        CLEAN_VERDICT,
    )
    os.makedirs(os.path.join(root, proj), exist_ok=True)
    dst = os.path.join(root, proj, sid + ".jsonl")
    os.rename(src, dst)
    return dst


def payload_file(text: str) -> str:
    """A local script fed to the pod, as the reads blocked 13 times before 2026-10-08."""
    fh = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
    fh.write(text)
    fh.close()
    return fh.name


READ_PY = payload_file("c=conn().cursor()\nc.execute('SELECT id FROM t WHERE id=2')\n")
UPDATE_PY = payload_file("c=conn().cursor()\nc.execute('UPDATE t SET a=1 WHERE id=2')\n")
COMMIT_PY = payload_file("c.execute(q)\nconn.commit()\n")


CASES = [
    # --- lectures et hors perimetre : doivent passer -------------------------------
    (PASS, "get pods", f"{K} get pods -n production-api"),
    (PASS, "logs", f"{K} logs -n production-api pod-abc --tail=50"),
    (PASS, "describe", f"{K} describe pod -n production-api pod-abc"),
    (PASS, "env grep dans le pod", f"{K} {E} pod-abc -- sh -c 'env | grep DB_'"),
    (PASS, "SELECT inline", f'{K} {E} pod-abc -- mysql -e "SELECT count(*) FROM t"'),
    (PASS, "SHOW GRANTS inline", f'{K} {E} pod-abc -- mysql -e "SHOW GRANTS"'),
    (PASS, "staging", f"{K} {E} -n staging pod-abc -- php artisan migrate"),
    (PASS, "aucune invocation", "git status -sb"),
    # --- portee : de la donnee, pas une commande (ajoutes le 2026-09-10) -----------
    (
        PASS,
        "heredoc local citant la commande",
        f"cat > notes.md <<'EOF'\nle vrai chemin d ecriture en prod est {K} {E} dans un pod, cf. script.py\nEOF",
    ),
    (
        PASS,
        "heredoc python ecrivant un fichier local",
        "python3 - <<'PY'\n# doc : " + f"{K} {E} -i pod -- php x.php" + "\nopen('/tmp/a','w')\nPY",
    ),
    (PASS, "grep sur la doc", f"grep -rn '{K} {E}' docs/"),
    # --- vrais positifs : doivent bloquer ------------------------------------------
    (
        BLOCK,
        "UPDATE inline",
        f'{K} {E} pod-abc -- mysql -e "UPDATE t SET a=1 WHERE id=2"',
    ),
    (BLOCK, "DELETE inline", f'{K} {E} pod-abc -- mysql -e "DELETE FROM t WHERE id=2"'),
    (BLOCK, "DROP TABLE", f'{K} {E} pod-abc -- mysql -e "DROP TABLE t"'),
    (
        BLOCK,
        "artisan import",
        f"{K} {E} pod-abc -- php artisan app:import:users f.csv",
    ),
    (BLOCK, "console delete", f"{K} {E} pod-abc -- php console app:purge-delete"),
    (BLOCK, "tinker", f"{K} {E} -it pod-abc -- php artisan tinker"),
    (
        PASS,
        "safe-relaunch.py (exempte par PROD_EXEC_ALLOWED_SCRIPTS)",
        "python3 ~/.claude/scripts/safe-relaunch.py reactivate 961112 --apply --out /tmp/x",
    ),
    (
        BLOCK,
        "safe-relaunch.py enchaine a un tinker direct",
        f"~/.claude/scripts/safe-relaunch.py checkaccess 1; {K} {E} -n production-workers "
        "deploy/worker-run-task -- php artisan tinker --execute 'x'",
    ),
    (
        BLOCK,
        "payload ecrit puis execute dans le pod",
        f"{K} {E} -i pod-abc -- sh -c 'cat > /tmp/x.php && php /tmp/x.php'",
    ),
    (
        BLOCK,
        "fichier pipe dans le pod",
        f"cat local.php | {K} {E} -i pod-abc -- php /tmp/x.php",
    ),
    (
        BLOCK,
        "interpreteur sur un chemin interne",
        f"{K} {E} pod-abc -- python3 /tmp/job.py",
    ),
    (
        BLOCK,
        "invocation apres un pipe",
        f"echo ok | xargs {K} {E} -i pod-abc -- php /tmp/x.php",
    ),
    (
        BLOCK,
        "invocation dans un sh -c",
        f"sh -c '{K} {E} -i pod-abc -- php /tmp/x.php'",
    ),
    # --- echappatoire seule : plus suffisante depuis le 2026-09-18 ------------------
    (
        BLOCK,
        "echappatoire seule, sans transcript",
        f'{K} {E} pod-abc -- mysql -e "UPDATE t SET a=1" # allow-prod-write',
    ),
    # --- echappatoire + verdict falsify : les cas qui comptent ----------------------
    (
        BLOCK,
        "echappatoire + aucun Relecteur dans la session",
        f'{K} {E} pod-abc -- mysql -e "UPDATE t SET a=1" # allow-prod-write',
        lambda: transcript_with("le plan touche 42 lignes, je continue"),
    ),
    (
        BLOCK,
        "echappatoire + dernier verdict casse",
        f'{K} {E} pod-abc -- mysql -e "UPDATE t SET a=1" # allow-prod-write',
        lambda: transcript_with("Relecteur: a cassé le verdict, deleted_at absent du WHERE."),
    ),
    (
        PASS,
        "echappatoire + verdict propre + snapshot + invariants",
        f'{K} {E} pod-abc -- mysql -e "UPDATE t SET a=1" # allow-prod-write',
        lambda: transcript_with(SNAPSHOT_WRITE, CLEAN_INVARIANTS, CLEAN_VERDICT),
    ),
    (
        PASS,
        "echappatoire + verdict propre (apostrophe typographique) + snapshot + invariants",
        f'{K} {E} pod-abc -- mysql -e "UPDATE t SET a=1" # allow-prod-write',
        lambda: transcript_with(
            SNAPSHOT_WRITE,
            CLEAN_INVARIANTS,
            "Relecteur: n’a pas pu casser, requête falsifiante jointe.",
        ),
    ),
    (
        PASS,
        "echappatoire + reouverture puis verdict propre (le dernier gagne) + snapshot + invariants",
        f'{K} {E} pod-abc -- mysql -e "UPDATE t SET a=1" # allow-prod-write',
        lambda: transcript_with(
            SNAPSHOT_WRITE,
            CLEAN_INVARIANTS,
            "Relecteur: a cassé le verdict, filtre deleted_at manquant.",
            "Relecteur: n'a pas pu casser, deleted_at ajouté et revérifié.",
        ),
    ),
    (
        BLOCK,
        "echappatoire + verdict propre puis regression (le dernier gagne) + snapshot + invariants",
        f'{K} {E} pod-abc -- mysql -e "UPDATE t SET a=1" # allow-prod-write',
        lambda: transcript_with(
            SNAPSHOT_WRITE,
            CLEAN_INVARIANTS,
            CLEAN_VERDICT,
            "Relecteur: a cassé le verdict, le perimetre a change depuis.",
        ),
    ),
    # --- echappatoire + snapshot : les cas ajoutes le 2026-09-19 --------------------
    (
        BLOCK,
        "echappatoire + verdict propre mais aucun snapshot",
        f'{K} {E} pod-abc -- mysql -e "UPDATE t SET a=1" # allow-prod-write',
        lambda: transcript_with(CLEAN_VERDICT),
    ),
    (
        PASS,
        "echappatoire + verdict propre + invariants + snapshot via Bash (SELECT ... > csv)",
        f'{K} {E} pod-abc -- mysql -e "UPDATE t SET a=1" # allow-prod-write',
        lambda: transcript_with(
            (
                "bash",
                f'{K} {E} pod-abc -- mysql -e "SELECT * FROM t WHERE id=2" > {SNAPSHOT_PATH}',
            ),
            CLEAN_INVARIANTS,
            CLEAN_VERDICT,
        ),
    ),
    (
        BLOCK,
        "echappatoire + verdict propre + fichier sans le mot before",
        f'{K} {E} pod-abc -- mysql -e "UPDATE t SET a=1" # allow-prod-write',
        lambda: transcript_with(("write", "/scratch/eng-1234-dump.json"), CLEAN_VERDICT),
    ),
    (
        BLOCK,
        "echappatoire + verdict propre + snapshot via un outil qui ne compte pas (Read)",
        f'{K} {E} pod-abc -- mysql -e "UPDATE t SET a=1" # allow-prod-write',
        lambda: transcript_with(("read", "/scratch/eng-1234-before-20260919.json"), CLEAN_VERDICT),
    ),
    # --- 2026-10-08 : lecture dont le payload est un fichier local lisible -------------
    (
        PASS,
        "fichier local en SELECT redirige",
        f"{K} {E} -i pod-abc -- python3 - < {READ_PY}",
    ),
    (
        PASS,
        "fichier local en SELECT pipe",
        f"cat {READ_PY} | {K} {E} -i pod-abc -- python3 -",
    ),
    (
        BLOCK,
        "fichier local avec UPDATE",
        f"{K} {E} -i pod-abc -- python3 - < {UPDATE_PY}",
    ),
    (
        BLOCK,
        "fichier local avec commit()",
        f"cat {COMMIT_PY} | {K} {E} -i pod-abc -- python3 -",
    ),
    (
        BLOCK,
        "fichier local absent",
        f"{K} {E} -i pod-abc -- python3 - < /nonexistent/x.py",
    ),
    (
        BLOCK,
        "fichier local propre mais lance un tinker",
        f"{K} {E} -i pod-abc -- php artisan tinker < {READ_PY}",
    ),
    (
        PASS,
        "grep d'un .php dans sh -c (pas un interpreteur)",
        f'{K} {E} pod-abc -- sh -c "grep -h apiKey /var/www/a/resources/config/prod.php"',
    ),
    # --- 2026-10-08 : snapshot ecrit par le script et imprime dans sa sortie ------------
    (
        PASS,
        "snapshot imprime, chemin absolu, present sur le disque",
        "uv run --with pymysql apply-eng1234-x.py --apply # allow-prod-write",
        lambda: printed_snapshot(relative=False, on_disk=True),
    ),
    (
        PASS,
        "snapshot imprime, chemin relatif, resolu dans le scratchpad",
        "uv run --with pymysql apply-eng1234-x.py --apply # allow-prod-write",
        lambda: printed_snapshot(relative=True, on_disk=True),
    ),
    (
        BLOCK,
        "snapshot imprime mais absent du disque",
        "uv run --with pymysql apply-eng1234-x.py --apply # allow-prod-write",
        lambda: printed_snapshot(relative=False, on_disk=False),
    ),
    (
        BLOCK,
        "snapshot imprime en relatif, absent du scratchpad",
        "uv run --with pymysql apply-eng1234-x.py --apply # allow-prod-write",
        lambda: printed_snapshot(relative=True, on_disk=False),
    ),
    # --- 2026-10-01 : scripts apply-*.py (pymysql, user DB en ecriture) ------------------
    (
        PASS,
        "apply script en dry run (defaut)",
        "uv run --with pymysql ~/.claude/scripts/apply-eng1234-x.py",
    ),
    (
        PASS,
        "apply script DRY=1 explicite",
        "DRY=1 uv run --with pymysql ~/.claude/scripts/apply-eng1234-x.py",
    ),
    (PASS, "script python sans drapeau de go", "python3 probe-eng1234.py --group 42"),
    (PASS, "DRY=0 sans script python (variable seule)", "DRY=0 echo nothing"),
    (
        PASS,
        "apply script vers staging",
        "DRY=0 uv run --with pymysql apply-x.py --host staging-db",
    ),
    (
        PASS,
        "safe-relaunch reste exempte",
        "python3 ~/.claude/scripts/safe-relaunch.py jobs --apply --ids 1,2",
    ),
    (
        BLOCK,
        "apply script DRY=0",
        "DRY=0 U=app P=x uv run --with pymysql ~/.claude/scripts/apply-eng1234-x.py",
    ),
    (
        BLOCK,
        "apply script --apply",
        "uv run --with pymysql ~/.claude/scripts/apply-eng1234-x.py --apply",
    ),
    (BLOCK, "script scratchpad --execute", "python3 /tmp/scratch/plan.py --execute"),
    (
        BLOCK,
        "apply script DRY=0 apres cd",
        "cd ~/.claude/scripts && DRY=0 python3 apply-eng1234-x.py",
    ),
    (
        BLOCK,
        "apply script + echappatoire sans artefacts",
        "DRY=0 uv run --with pymysql apply-eng1234-x.py # allow-prod-write",
        lambda: transcript_with("rien"),
    ),
    (
        PASS,
        "apply script + echappatoire + verdict propre + snapshot + invariants",
        "DRY=0 uv run --with pymysql apply-eng1234-x.py # allow-prod-write",
        lambda: transcript_with(SNAPSHOT_WRITE, CLEAN_INVARIANTS, CLEAN_VERDICT),
    ),
    # --- invariants lus dans une sortie d'outil (ajoutes le 2026-10-02) --------------
    (
        PASS,
        "invariants dans la sortie du dry-run, pas dans le texte",
        "DRY=0 uv run --with pymysql apply-eng1234-x.py # allow-prod-write",
        lambda: transcript_with(SNAPSHOT_WRITE, CLEAN_VERDICT, ("result", CLEAN_INVARIANTS)),
    ),
    (
        BLOCK,
        "sortie du dry-run avec un forbidden non nul",
        "DRY=0 uv run --with pymysql apply-eng1234-x.py # allow-prod-write",
        lambda: transcript_with(
            SNAPSHOT_WRITE,
            CLEAN_VERDICT,
            ("result", "INVARIANTS:\nexpected_rows: 5\nforbidden[hors perimetre]: 3\n"),
        ),
    ),
    (
        PASS,
        "message de blocage du hook apres le bloc propre (erreur ignoree)",
        "DRY=0 uv run --with pymysql apply-eng1234-x.py # allow-prod-write",
        lambda: transcript_with(
            SNAPSHOT_WRITE,
            CLEAN_VERDICT,
            ("result", CLEAN_INVARIANTS),
            ("error", "BLOCKED: no clean `INVARIANTS:` block in this session"),
        ),
    ),
    (
        BLOCK,
        "texte propre puis sortie d'outil non propre (le dernier gagne)",
        "DRY=0 uv run --with pymysql apply-eng1234-x.py # allow-prod-write",
        lambda: transcript_with(
            SNAPSHOT_WRITE,
            CLEAN_VERDICT,
            CLEAN_INVARIANTS,
            ("result", "INVARIANTS:\nexpected_rows: 5\nforbidden[x]: 1\n"),
        ),
    ),
    # --- ligne Relecteur lue dans une sortie Bash (ajoutes le 2026-10-02) -------------
    (
        PASS,
        "verdict imprime par Bash, pas dans le texte",
        "DRY=0 uv run --with pymysql apply-eng1234-x.py # allow-prod-write",
        lambda: transcript_with(SNAPSHOT_WRITE, CLEAN_INVARIANTS, ("ran", "Bash", CLEAN_VERDICT + "\n")),
    ),
    (
        BLOCK,
        "cat de falsify/SKILL.md (cite les deux lignes, la propre en dernier)",
        "DRY=0 uv run --with pymysql apply-eng1234-x.py # allow-prod-write",
        lambda: transcript_with(
            SNAPSHOT_WRITE,
            CLEAN_INVARIANTS,
            (
                "ran",
                "Bash",
                "# Falsify\n- `Relecteur: a cassé le verdict.`\n- `" + CLEAN_VERDICT + "`\n",
            ),
        ),
    ),
    (
        BLOCK,
        "resultat d'Agent qui commence par un verdict propre",
        "DRY=0 uv run --with pymysql apply-eng1234-x.py # allow-prod-write",
        lambda: transcript_with(SNAPSHOT_WRITE, CLEAN_INVARIANTS, ("ran", "Agent", CLEAN_VERDICT)),
    ),
    (
        BLOCK,
        "sortie Bash en erreur qui commence par un verdict propre",
        "DRY=0 uv run --with pymysql apply-eng1234-x.py # allow-prod-write",
        lambda: transcript_with(SNAPSHOT_WRITE, CLEAN_INVARIANTS, ("ran-error", "Bash", CLEAN_VERDICT)),
    ),
    (
        BLOCK,
        "verdict propre en texte puis Bash imprime une reouverture (le dernier gagne)",
        "DRY=0 uv run --with pymysql apply-eng1234-x.py # allow-prod-write",
        lambda: transcript_with(
            SNAPSHOT_WRITE,
            CLEAN_INVARIANTS,
            CLEAN_VERDICT,
            ("ran", "Bash", "Relecteur: a cassé le verdict.\n"),
        ),
    ),
]


def verdict(command, transcript_path=None, env=None):
    tool_input = {"command": command}
    payload = {"tool_name": "Bash", "tool_input": tool_input}
    if transcript_path:
        payload["transcript_path"] = transcript_path
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=ENV if env is None else env,
    )
    return BLOCK if p.returncode == 2 else PASS


def main():
    fails = 0
    for case in CASES:
        expected, name, command = case[0], case[1], case[2]
        transcript_path = case[3]() if len(case) > 3 else None
        try:
            got = verdict(command, transcript_path)
        finally:
            if transcript_path:
                os.unlink(transcript_path)
        ok = got == expected
        fails += not ok
        print(f"{'OK  ' if ok else 'FAIL'}  {name:55} attendu={expected:6} obtenu={got}")
    # Sans PROD_EXEC_ALLOWED_SCRIPTS, aucun script n'est exempte.
    bare = {k: v for k, v in ENV.items() if k != "PROD_EXEC_ALLOWED_SCRIPTS"}
    got = verdict("python3 ~/.claude/scripts/safe-relaunch.py jobs --apply --ids 1,2", env=bare)
    ok = got == BLOCK
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'}  {'sans variable, safe-relaunch --apply bloque':55} attendu={BLOCK:6} obtenu={got}")
    total = len(CASES) + 1
    print(f"\n{total - fails}/{total}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
