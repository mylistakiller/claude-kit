#!/usr/bin/env python3
"""Suite de tests de `memory-review.py reindex`.

    python3 scripts/tests/test-memory-review-reindex.py   (depuis la racine du kit)

Tourne sur un store JETABLE (tempfile), jamais sur les vraies memoires : le module est
importe puis ses constantes MEM / INDEX / LOG / TRASH sont repointees. Ajoutee le 2026-09-11 avec la
correction qui apprend a reindex a reecrire UN segment d'une ligne d'index partagee, au lieu
de refuser et de renvoyer a une edition manuelle de MEMORY.md.
"""

import argparse
import importlib.util
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile

SCRIPT = str(Path(__file__).resolve().parents[1] / "memory-review.py")

spec = importlib.util.spec_from_file_location("memrev", SCRIPT)
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)
REAL_MEM = mr.MEM


def memory_file(store, name, description):
    path = os.path.join(store, name + ".md")
    io.open(path, "w", encoding="utf-8").write(
        '---\nname: %s\ndescription: "%s"\nmetadata:\n  type: reference\n---\n\ncorps\n' % (name, description)
    )


def run(store, index_line, name, **kw):
    """Repointe le module sur un store jetable, lance reindex, rend (sortie, exit, index)."""
    mr.MEM, mr.INDEX, mr.LOG, mr.TRASH = (
        store,
        os.path.join(store, "MEMORY.md"),
        os.path.join(store, ".log.json"),
        os.path.join(store, ".trash"),
    )
    # une constante de chemin oubliee ecrirait dans le vrai store
    leaks = sorted(k for k, v in vars(mr).items() if isinstance(v, str) and v.startswith(REAL_MEM))
    if leaks:
        raise SystemExit("constantes encore sur le vrai store : %s" % ", ".join(leaks))
    io.open(mr.INDEX, "w", encoding="utf-8").write("# Memory Index\n\n" + index_line + "\n")
    args = argparse.Namespace(name=name, hook=None, label=None, dry_run=False)
    for k, v in kw.items():
        setattr(args, k, v)
    out, code = io.StringIO(), 0
    keep = sys.stdout
    sys.stdout = out
    try:
        mr.cmd_reindex(args)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.stdout = keep
    return (
        out.getvalue(),
        code,
        io.open(mr.INDEX, encoding="utf-8").read().splitlines()[-1],
    )


SHARED = "- [Serveurs MCP](aaa.md); [Sept hooks](bbb.md) `-- allow-full-scan`, verdict sans preuve; [MCP sql](ccc.md)"
PREFIXED = "- db-replica : [acces stats](aaa.md); [table purgee](bbb.md) PIEGE info_schema"
SOLO = "- [Sept hooks](bbb.md) : vieux crochet"

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


@case("ligne solitaire : crochet regenere depuis la description")
def _(store):
    out, code, line = run(store, SOLO, "bbb")
    return code == 0 and line == "- [Sept hooks](bbb.md) : desc de bbb", line


@case("ligne partagee sans texte : refuse et donne la commande")
def _(store):
    out, code, line = run(store, SHARED, "bbb")
    return code == 2 and "--hook" in out and line == SHARED, line


@case("ligne partagee, segment du MILIEU : seul ce segment bouge")
def _(store):
    out, code, line = run(store, SHARED, "bbb", hook="nouveau crochet")
    want = "- [Serveurs MCP](aaa.md); [Sept hooks](bbb.md) nouveau crochet; [MCP sql](ccc.md)"
    return code == 0 and line == want, line


@case("ligne partagee, segment de TETE : les voisines sont intactes")
def _(store):
    out, code, line = run(store, SHARED, "aaa", hook="deux serveurs")
    want = (
        "- [Serveurs MCP](aaa.md) : deux serveurs; [Sept hooks](bbb.md) `-- allow-full-scan`,"
        " verdict sans preuve; [MCP sql](ccc.md)"
    )
    return code == 0 and line == want, line


@case("ligne partagee, segment de QUEUE")
def _(store):
    out, code, line = run(store, SHARED, "ccc", hook="DESCRIBE casse")
    want = (
        "- [Serveurs MCP](aaa.md); [Sept hooks](bbb.md) `-- allow-full-scan`, verdict sans"
        " preuve; [MCP sql](ccc.md) : DESCRIBE casse"
    )
    return code == 0 and line == want, line


@case("libelle seul : le crochet existant est preserve")
def _(store):
    out, code, line = run(store, SHARED, "bbb", label="Huit hooks")
    want = "- [Serveurs MCP](aaa.md); [Huit hooks](bbb.md) `-- allow-full-scan`, verdict sans preuve; [MCP sql](ccc.md)"
    return code == 0 and line == want, line


@case("--hook vide : ne garde que le lien")
def _(store):
    out, code, line = run(store, SHARED, "bbb", hook="")
    want = "- [Serveurs MCP](aaa.md); [Sept hooks](bbb.md); [MCP sql](ccc.md)"
    return code == 0 and line == want, line


@case("prefixe de ligne conserve mot pour mot")
def _(store):
    out, code, line = run(store, PREFIXED, "aaa", hook="port 5432")
    want = "- db-replica : [acces stats](aaa.md) : port 5432; [table purgee](bbb.md) PIEGE info_schema"
    return code == 0 and line == want, line


@case("dry-run n'ecrit rien")
def _(store):
    out, code, line = run(store, SHARED, "bbb", hook="x", dry_run=True)
    return code == 0 and line == SHARED and "dry-run" in out, line


@case("lien present deux fois sur la ligne : refuse")
def _(store):
    dup = "- [un](bbb.md); [deux](bbb.md)"
    out, code, line = run(store, dup, "bbb", hook="x")
    return code != 0 and line == dup, line


@case("memoire orpheline de l'index : refuse")
def _(store):
    out, code, line = run(store, "- [Serveurs MCP](aaa.md)", "bbb", hook="x")
    return code != 0, line


def with_subindex(store, sub_line, fn):
    """Pose une ligne dans un sous-index _index-test.md, lance fn, rend (resultat, sous-index)."""
    sub = os.path.join(store, "_index-test.md")
    io.open(sub, "w", encoding="utf-8").write("# Test\n\n" + sub_line + "\n")
    try:
        return fn(), io.open(sub, encoding="utf-8").read().splitlines()[-1]
    finally:
        os.remove(sub)


@case("sous-index : reindex reecrit la ligne du sous-index, MEMORY.md intact")
def _(store):
    (out, code, root), sub = with_subindex(
        store,
        "- [Serveurs MCP](bbb.md) : vieux",
        lambda: run(store, "- [Racine](aaa.md) : garde", "bbb", hook="neuf"),
    )
    ok = code == 0 and sub == "- [Serveurs MCP](bbb.md) : neuf" and root == "- [Racine](aaa.md) : garde"
    return ok, (code, sub, root)


@case("sous-index : kill retire le lien du sous-index, pas un orphelin")
def _(store):
    memory_file(store, "ddd", "desc de ddd")

    def kill():
        run(store, "- [Racine](aaa.md)", "aaa", hook="x")  # repointe le module sur le store
        out, keep = io.StringIO(), sys.stdout
        sys.stdout = out
        try:
            mr.cmd_kill(argparse.Namespace(name="ddd", note=""))
        finally:
            sys.stdout = keep
        return out.getvalue()

    out, sub = with_subindex(store, "- Theme : [a](ccc.md); [d](ddd.md)", kill)
    return "orphan" not in out and sub == "- Theme : [a](ccc.md)", (out, sub)


@case("sous-index : compte dans l'index, jamais comme une memoire")
def _(store):
    def probe():
        run(store, "- [Racine](aaa.md)", "aaa", hook="x")
        return [f for f in mr.memories() if f.startswith("_index-")], "(bbb.md)" in mr.index_text()

    (as_memory, linked), _sub = with_subindex(store, "- [b](bbb.md)", probe)
    return as_memory == [] and linked, (as_memory, linked)


def main():
    store = tempfile.mkdtemp(prefix="memrev-test-")
    try:
        for n in ("aaa", "bbb", "ccc"):
            memory_file(store, n, "desc de " + n)
        fails = 0
        for name, fn in CASES:
            try:
                ok, got = fn(store)
            except Exception as exc:  # un plantage est un echec, pas une interruption
                ok, got = False, f"EXCEPTION {exc!r}"
            fails += not ok
            print(f"{'OK  ' if ok else 'FAIL'}  {name}")
            if not ok:
                print(f"        obtenu: {got}")
        print(f"\n{len(CASES) - fails}/{len(CASES)}")
        sys.exit(1 if fails else 0)
    finally:
        shutil.rmtree(store, ignore_errors=True)


if __name__ == "__main__":
    main()
