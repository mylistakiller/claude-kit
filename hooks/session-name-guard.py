#!/usr/bin/env python3
"""Stop hook: refuse to end a turn while the session still has no name.

CLAUDE.md asks for every session to be named with scripts/name-session.sh as soon as the subject is
clear. That rule was prose, so it got skipped: on 24/09/2026, 16 of the day's 17 sessions had
no title in /resume. This makes it mechanical: once a real prompt has been answered and the
transcript still carries no custom-title entry, the turn is blocked and Claude is sent back
to name it.

Exit codes: 0 = let the turn end, 2 = block (stderr goes back to Claude).
Fails open on anything unexpected, and never blocks twice in a row (stop_hook_active).

Configuration: KIT_HEADLESS=1 disables the check (headless runs have no session to name).
"""

import json
import os
import sys

SKIP_PREFIXES = ("<local-command", "<command-", "<system-reminder", "<bash-")
NAME_SESSION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "name-session.sh"
)


def real_prompt(entry: dict) -> bool:
    if entry.get("type") != "user" or entry.get("isMeta") or "toolUseResult" in entry:
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    text = (content or "").strip()
    return bool(text) and not text.startswith(SKIP_PREFIXES)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("stop_hook_active"):
        return 0
    # Headless runs (claude -p from a script) have no session to name: the script that
    # launches them sets KIT_HEADLESS=1.
    if os.environ.get("KIT_HEADLESS") == "1":
        return 0
    path, sid = data.get("transcript_path"), data.get("session_id")
    if not path or not sid:
        return 0
    prompts = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"custom-title"' in line:
                    return 0
                try:
                    if real_prompt(json.loads(line)):
                        prompts += 1
                except Exception:
                    continue
    except OSError:
        return 0
    if prompts == 0:
        return 0
    print(
        "Cette session n'a pas de nom. Nomme-la maintenant, avant de rendre la main :\n"
        f'  {NAME_SESSION} "<nom>" {sid}\n'
        "Nom = id du ticket + 2-3 mots (eng-994-login-timeout), sinon le sujet "
        "(stats-export-sonde). Minuscules, tirets, pas de date. Si le sujet n'est vraiment "
        "pas encore clair, prends le meilleur nom provisoire : il se renomme plus tard.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
