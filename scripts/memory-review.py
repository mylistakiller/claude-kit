#!/usr/bin/env python3
"""Daily case-by-case triage of a Claude Code project memory store.

The store grew into an undifferentiated pile: closed tickets kept as if live, entries that
self-declare PÉRIMÉ, unapplied writes ("NON APPLIQUÉ", "non posté") that are really a
backlog, and index lines pointing at files that moved. Recall pulls those in as if true.

This ranks what to look at next and applies the decision cleanly: deleting a memory also
strips its MEMORY.md index line and keeps a copy in .trash/, so a kill is reversible.
The judgement itself is the user's, case per case: the `memory-review` skill drives the QCM.

  candidates [--n 8]   ranked queue, with why each one surfaced
  show <name>          full file
  keep <name> [--note] stamp reviewed today, leave the file alone
  kill <name>          -> .trash/, drop its MEMORY.md line(s)
  reindex <name>       rewrite its MEMORY.md entry after a reduction (one segment only
                       when the line carries several memories)
  stats                store health: orphans, dangling links, index drift
  due                  exit 0 if today's review has not run (for the SessionStart hook)

State lives in .review-log.json next to the memories (never inside them, so the recall
format stays untouched).

The store is $CLAUDE_MEMORY_DIR when set, else ~/.claude/projects/<slug>/memory where <slug>
is the project root ($CLAUDE_PROJECT_DIR, else the git toplevel of the cwd, else the cwd)
with every non-alphanumeric character turned into "-", the way Claude Code names its project
directories.
Ticket ids surfaced per memory match $MEMORY_TICKET_PREFIXES (comma list, e.g. "ABC,OPS"),
or any uppercase KEY-123 when unset.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime


def project_root() -> str:
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if root:
        return root
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], check=False, capture_output=True, text=True, timeout=10
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.getcwd()


def memory_dir() -> str:
    explicit = os.environ.get("CLAUDE_MEMORY_DIR")
    if explicit:
        return os.path.expanduser(explicit)
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(project_root()))
    return os.path.join(os.path.expanduser("~/.claude/projects"), slug, "memory")


MEM = memory_dir()
INDEX = os.path.join(MEM, "MEMORY.md")
# MEMORY.md is loaded into every session, so it only carries the cross-cutting entries and one
# pointer per theme; each theme's lines live in a sub-index read on demand. Sub-indexes sit at
# the store root so their "(file.md)" links stay valid relative paths.
SUBINDEX_PREFIX = "_index-"
LOG = os.path.join(MEM, ".review-log.json")
TRASH = os.path.join(MEM, ".trash")
TODAY = date.today().isoformat()

# Self-declared rot, and unfinished work parked in a memory instead of a tracker.
# (marker, why, case_sensitive). The ALL-CAPS ones are a convention of the store and MUST
# stay case-sensitive: "RESTE" is also the French verb ("la cible reste sous la source"), and
# "PERIME" is a substring of "perimetre": both produced false candidates.
FLAGS_SPEC = [
    ("PÉRIMÉ", "self-declares PÉRIMÉ", True),
    ("PÉRIMÉE", "self-declares PÉRIMÉ", True),
    ("PERIME", "self-declares PÉRIMÉ", True),
    ("NON APPLIQUÉ", "unapplied prod writes parked here", True),
    ("NON APPLIQUÉES", "unapplied prod writes parked here", True),
    ("RESTE", "open loop (RESTE ...)", True),
    ("TODO", "TODO left in a memory", True),
    ("FIXME", "TODO left in a memory", True),
    ("non posté", "a Linear comment was never posted", False),
    ("non appliquée", "unapplied prod writes parked here", False),
    ("à trancher", "undecided question parked here", False),
    ("non tranchée", "undecided question parked here", False),
    ("non tranchés", "undecided question parked here", False),
]
FLAGS = {token: why for token, why, _ in FLAGS_SPEC}

_PREFIXES = [p.strip() for p in os.environ.get("MEMORY_TICKET_PREFIXES", "").split(",") if p.strip()]
TICKET_RE = (
    re.compile(r"\b(?:" + "|".join(map(re.escape, _PREFIXES)) + r")-\d+\b", re.IGNORECASE)
    if _PREFIXES
    else re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
)


def flags_in(text: str) -> list:
    """Péremption markers present as whole words: "perimetre" in a filename is not one."""
    out = []
    for token, why, cased in FLAGS_SPEC:
        flags = 0 if cased else re.I
        if re.search(rf"(?<![\w]){re.escape(token)}(?![\w])", text, flags) and why not in out:
            out.append(why)
    return out


def load_log() -> dict:
    try:
        return json.load(open(LOG))
    except Exception:
        return {}


def save_log(log: dict) -> None:
    json.dump(log, open(LOG, "w"), indent=2, ensure_ascii=False, sort_keys=True)


def memories() -> list:
    return sorted(
        f for f in os.listdir(MEM)
        if f.endswith(".md") and f != "MEMORY.md" and not f.startswith(SUBINDEX_PREFIX)
    )


def frontmatter(text: str) -> dict:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    fm = {}
    if not m:
        return fm
    for line in m.group(1).splitlines():
        mm = re.match(r"\s*([\w-]+):\s*(.*)", line)
        if mm:
            fm[mm.group(1)] = mm.group(2).strip().strip("\"'")
    return fm


def index_files() -> list:
    subs = sorted(
        os.path.join(MEM, f) for f in os.listdir(MEM)
        if f.startswith(SUBINDEX_PREFIX) and f.endswith(".md")
    )
    return [INDEX] + subs


def read_index(path: str) -> str:
    try:
        return open(path, encoding="utf-8").read()
    except Exception:
        return ""


def index_text() -> str:
    return "".join(read_index(p) for p in index_files())


def age_days(fm: dict, path: str) -> int:
    stamp = fm.get("modified") or ""
    try:
        d = datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
    except Exception:
        d = date.fromtimestamp(os.path.getmtime(path))
    return (date.today() - d).days


def describe(fname: str, idx: str, log: dict) -> dict:
    path = os.path.join(MEM, fname)
    text = open(path, encoding="utf-8", errors="replace").read()
    fm = frontmatter(text)
    name = fm.get("name") or fname[:-3]
    reasons, score = [], 0

    entry = log.get(fname) or {}
    if not entry.get("reviewed"):
        reasons.append("never reviewed")
        score += 40
    else:
        since = (date.today() - date.fromisoformat(entry["reviewed"])).days
        score += min(since // 7, 20)
        reasons.append(f"last reviewed {entry['reviewed']} ({since}d ago)")

    found = flags_in(text)
    if found:
        reasons.append(found[0])
        score += 25

    days = age_days(fm, path)
    score += min(days // 15, 20)
    reasons.append(f"{days}d since last update")

    mtype = fm.get("type") or ""
    if not mtype:
        mtype = re.search(r"type:\s*(\w+)", text).group(1) if re.search(r"type:\s*(\w+)", text) else "?"
    if mtype == "project" and days > 60:
        reasons.append("project memory older than 60d (work likely closed)")
        score += 15

    if fname not in idx:
        reasons.append("ORPHAN: not linked from MEMORY.md")
        score += 30

    dangling = [
        l for l in set(re.findall(r"\[\[([^\]]+)\]\]", text))
        if not os.path.exists(os.path.join(MEM, f"{l}.md"))
    ]
    if dangling:
        reasons.append(f"dangling links: {', '.join(sorted(dangling)[:3])}")
        score += 5

    tickets = sorted({t.upper() for t in TICKET_RE.findall(text)})

    return {
        "file": fname,
        "name": name,
        "type": mtype,
        "score": score,
        "age_days": days,
        "tickets": tickets[:6],
        "description": fm.get("description", "")[:400],
        "reasons": reasons,
        "size_lines": text.count("\n") + 1,
    }


def cmd_candidates(args) -> None:
    idx, log = index_text(), load_log()
    rows = [describe(f, idx, log) for f in memories()]
    rows = [r for r in rows if (log.get(r["file"], {}).get("reviewed") != TODAY)]
    rows.sort(key=lambda r: (-r["score"], -r["age_days"]))
    rows = rows[: args.n]
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    for i, r in enumerate(rows, 1):
        print(f"{i}. {r['name']}  [{r['type']}, {r['size_lines']} lines, score {r['score']}]")
        print(f"   {r['description']}")
        if r["tickets"]:
            print(f"   tickets: {', '.join(r['tickets'])}")
        print(f"   why now: {'; '.join(r['reasons'])}\n")


def cmd_show(args) -> None:
    path = os.path.join(MEM, args.name if args.name.endswith(".md") else args.name + ".md")
    if not os.path.exists(path):
        sys.exit(f"no such memory: {args.name}")
    print(open(path, encoding="utf-8").read())


def cmd_keep(args) -> None:
    fname = args.name if args.name.endswith(".md") else args.name + ".md"
    if not os.path.exists(os.path.join(MEM, fname)):
        sys.exit(f"no such memory: {args.name}")
    log = load_log()
    entry = log.setdefault(fname, {})
    entry["reviewed"] = TODAY
    entry["verdict"] = args.verdict
    if args.note:
        entry["note"] = args.note
    save_log(log)
    print(f"{fname}: marked {args.verdict} on {TODAY}")


def cmd_kill(args) -> None:
    fname = args.name if args.name.endswith(".md") else args.name + ".md"
    src = os.path.join(MEM, fname)
    if not os.path.exists(src):
        sys.exit(f"no such memory: {args.name}")

    os.makedirs(TRASH, exist_ok=True)
    shutil.move(src, os.path.join(TRASH, f"{TODAY}.{fname}"))

    # An index line can carry SEVERAL memories ("- Réseau X : [a](a.md); [b](b.md)").
    # Dropping the whole line orphans the survivors, so remove only this file's link and
    # keep the line unless nothing else is left on it.
    dropped, trimmed = [], []
    for index_path in index_files():
        kept, changed = kill_from_index(read_index(index_path), fname, dropped, trimmed)
        if changed:
            open(index_path, "w", encoding="utf-8").write("".join(kept))

    log = load_log()
    log[fname] = {"reviewed": TODAY, "verdict": "killed", "note": args.note or ""}
    save_log(log)

    print(f"{fname} -> .trash/{TODAY}.{fname}")
    for d in dropped:
        print(f"index line removed: {d.strip()}")
    for before, after in trimmed:
        print(f"index line trimmed (other memories on it survive):\n  - {before.strip()}\n  + {after.strip()}")
    if not dropped and not trimmed:
        print("WARNING: no index line referenced this file (was already an orphan)")


def kill_from_index(text: str, fname: str, dropped: list, trimmed: list):
    kept, changed = [], False
    for line in text.splitlines(keepends=True):
        if f"({fname})" not in line:
            kept.append(line)
            continue
        changed = True
        stripped = re.sub(r"\[[^\]]*\]\(" + re.escape(fname) + r"\)", "", line)
        remaining = re.findall(r"\[[^\]]*\]\(([\w.-]+\.md)\)", stripped)
        if not remaining:
            dropped.append(line.rstrip())
            continue
        # Tidy the punctuation the removed link left behind.
        stripped = re.sub(r";\s*;", ";", stripped)
        stripped = re.sub(r"(:|\u2014)\s*;\s*", r"\1 ", stripped)
        stripped = re.sub(r"(:|;|,)\s*(;|$)", r"\2", stripped.rstrip("\n"))
        stripped = re.sub(r"\s{2,}", " ", stripped)
        stripped = re.sub(r"[\s;,]+$", "", stripped)
        stripped = re.sub(r"\s+\u2014\s*$", "", stripped)
        # Le lien retiré portait parfois le libellé de la ligne : ne pas laisser un tiret long orphelin en tete de ligne.
        stripped = re.sub(r"^(\s*-\s*)[\u2014;:,]\s*", r"\1", stripped)
        trimmed.append((line.rstrip(), stripped))
        kept.append(stripped + "\n")
    return kept, changed



def default_hook(fm: dict) -> str:
    """Un crochet d'index court, tire de la description du fichier."""
    d = (fm.get("description") or "").strip()
    if not d:
        return ""
    if len(d) <= 150:
        return d
    cut = max(d.rfind(" ; ", 0, 150), d.rfind(", ", 0, 150), d.rfind(" ", 0, 150))
    return d[: cut if cut > 60 else 150].rstrip(" ,;") + "..."


# Un segment d'une ligne d'index : le lien d'une memoire et le crochet qui le suit, jusqu'au
# "; " qui introduit la memoire voisine. Ce qui precede le premier lien (un prefixe de ligne
# comme "- prod-stats-2 : ") et les segments voisins ne sont jamais touches.
SEGMENT_END = re.compile(r"\s*;\s+(?=\[)")


def segment_span(line: str, fname: str):
    """(debut, fin) du segment de `fname` dans `line`, ou None si le lien n'y est pas.

    Le lien doit apparaitre une seule fois : deux occurrences du meme fichier sur une ligne
    sont ambigues et l'appelant refuse plutot que de deviner.
    """
    link = re.compile(r"\[[^\]]*\]\(" + re.escape(fname) + r"\)")
    found = list(link.finditer(line))
    if len(found) != 1:
        return None
    start = found[0].start()
    nxt = SEGMENT_END.search(line, found[0].end())
    return start, (nxt.start() if nxt else len(line))


def current_hook(segment: str, fname: str) -> str:
    """Le crochet deja en place dans le segment, sans son separateur."""
    after = segment[segment.index(f"({fname})") + len(f"({fname})"):]
    return re.sub(r"^\s*(:|\u2014|,)?\s*", "", after).strip()


def segment_joiner(segment: str, fname: str) -> str:
    """Le separateur entre le lien et son crochet, tel qu'il est deja ecrit sur la ligne.

    Le store melange " : ", le tiret long, ", " et l'espace nu ; on garde celui d'origine pour ne pas
    reecrire la ponctuation des voisins par ricochet.
    """
    after = segment[segment.index(f"({fname})") + len(f"({fname})"):]
    m = re.match(r"\s*(:|\u2014|,)?\s*", after)
    if after.strip() and m:
        sep = (m.group(1) or "").strip()
        return f" {sep} " if sep else " "
    return " : "


def cmd_reindex(args) -> None:
    """Reecrit l'entree d'index d'une memoire apres une reduction.

    Sur une ligne qui porte plusieurs memoires, seul le segment de CETTE memoire est
    reecrit : le prefixe de ligne et les voisines sont laisses mot pour mot.
    """
    fname = args.name if args.name.endswith(".md") else args.name + ".md"
    path = os.path.join(MEM, fname)
    if not os.path.exists(path):
        sys.exit(f"no such memory: {args.name}")
    fm = frontmatter(open(path, encoding="utf-8").read())

    hits = []
    for index_path in index_files():
        file_lines = read_index(index_path).splitlines(keepends=True)
        hits += [(index_path, file_lines, i) for i, l in enumerate(file_lines) if f"({fname})" in l]
    if not hits:
        sys.exit(f"no index line references {fname} (orphan: add one by hand)")
    if len(hits) > 1:
        sys.exit(f"{len(hits)} index lines reference {fname}: fix by hand, refusing to guess")

    index_path, lines, i = hits[0]
    line = lines[i].rstrip("\n")
    span = segment_span(line, fname)
    if span is None:
        sys.exit(f"{fname} appears more than once on its index line: fix by hand")
    start, end = span
    segment = line[start:end]
    shared = len(re.findall(r"\[[^\]]*\]\([\w.-]+\.md\)", line)) > 1

    current_label = re.match(r"\[([^\]]*)\]", segment).group(1)
    label = args.label or current_label
    if shared and args.hook is None and args.label is None:
        print("Ligne partagee : le crochet y est un mnemonique ecrit a la main, je ne le devine pas.")
        print(f"  ligne    : {line.strip()}")
        print(f"  segment  : {segment.strip()}")
        print("Donne le texte et le script fait l'edition (MEMORY.md ne s'edite pas a la main) :")
        print(f'  memory-review.py reindex {args.name} --hook "<mnemonique>" [--label "<libelle>"]')
        print('  (--hook "" retire le crochet et ne garde que le lien)')
        sys.exit(2)
    hook = args.hook if args.hook is not None else (
        current_hook(segment, fname) if shared else default_hook(fm)
    )
    joiner = segment_joiner(segment, fname) if shared else " : "
    new_segment = f"[{label}]({fname})" + (f"{joiner}{hook}" if hook else "")
    new_line = line[:start] + new_segment + line[end:]

    if new_line == line:
        print("index line already up to date")
        return
    print(f"  - {line.strip()}")
    print(f"  + {new_line.strip()}")
    if shared:
        others = [f for _, f in re.findall(r"\[([^\]]*)\]\(([\w.-]+\.md)\)", line) if f != fname]
        print(f"  (ligne partagee, {len(others)} voisine(s) laissee(s) intactes : {', '.join(others)})")
        print("  relis le diff : un crochet place apres le DERNIER lien peut valoir pour toute la ligne")
    if args.dry_run:
        print("(dry-run, nothing written)")
        return
    lines[i] = new_line + "\n"
    open(index_path, "w", encoding="utf-8").write("".join(lines))
    print(f"{os.path.basename(index_path)} line {i + 1} rewritten")

def cmd_stats(_args) -> None:
    idx, log = index_text(), load_log()
    files = memories()
    orphans = [f for f in files if f not in idx]
    linked = set(re.findall(r"\(([\w.-]+\.md)\)", idx))
    ghosts = sorted(f for f in linked - set(files) if not f.startswith(SUBINDEX_PREFIX))
    reviewed = [f for f in files if log.get(f, {}).get("reviewed")]
    flagged = [
        f for f in files
        if flags_in(open(os.path.join(MEM, f), encoding="utf-8", errors="replace").read())
    ]
    print(f"memories            : {len(files)}")
    print(f"reviewed at least 1x: {len(reviewed)}  ({len(files) - len(reviewed)} never)")
    print(f"index lines         : {sum(1 for l in idx.splitlines() if l.strip().startswith('- '))}")
    print(f"orphans (no index)  : {len(orphans)}" + (f" -> {', '.join(orphans[:5])}" if orphans else ""))
    print(f"index -> dead file  : {len(ghosts)}" + (f" -> {', '.join(ghosts[:5])}" if ghosts else ""))
    print(f"carrying a stale flag: {len(flagged)}")
    print(f"killed to date      : {sum(1 for v in log.values() if v.get('verdict') == 'killed')}")


def cmd_due(_args) -> None:
    log = load_log()
    done = sum(1 for v in log.values() if v.get("reviewed") == TODAY)
    never = sum(1 for f in memories() if not load_log().get(f, {}).get("reviewed"))
    if done:
        print(f"already reviewed {done} memories today")
        sys.exit(1)
    print(f"memory review due: {len(memories())} memories, {never} never reviewed")
    sys.exit(0)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("candidates"); c.add_argument("--n", type=int, default=8)
    c.add_argument("--json", action="store_true"); c.set_defaults(func=cmd_candidates)
    s = sub.add_parser("show"); s.add_argument("name"); s.set_defaults(func=cmd_show)
    k = sub.add_parser("keep"); k.add_argument("name"); k.add_argument("--note")
    k.add_argument("--verdict", default="kept"); k.set_defaults(func=cmd_keep)
    d = sub.add_parser("kill"); d.add_argument("name"); d.add_argument("--note")
    d.set_defaults(func=cmd_kill)
    r = sub.add_parser("reindex"); r.add_argument("name")
    r.add_argument("--hook"); r.add_argument("--label"); r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_reindex)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    sub.add_parser("due").set_defaults(func=cmd_due)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
