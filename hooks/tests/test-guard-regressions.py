#!/usr/bin/env python3
"""Regression checks run only on local hooks and disposable artifacts, never on production."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_hook(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "hooks" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


APPROVE = load_hook("auto-approve-readonly")
EXEC = load_hook("guard-prod-exec")


def hook_exit(name, tool, inp):
    result = subprocess.run(
        [sys.executable, str(ROOT / "hooks" / f"{name}.py")],
        input=json.dumps({"tool_name": tool, "tool_input": inp}),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 2):
        raise AssertionError(result.stderr)
    return result.returncode


class GuardRegressionTests(unittest.TestCase):
    def test_auto_approval_rejects_writers_and_arbitrary_executables(self):
        for command in [
            "awk 'BEGIN { system(\"touch /tmp/x\") }'",
            "sed 'w /tmp/x' README.md",
            "sort -o /tmp/x README.md",
            "git diff --output=/tmp/x",
            "/tmp/rogue/cat README.md",
            "rg --pre=arbitrary-command pattern .",
            "rg --hostname-bin arbitrary-command pattern .",
            "uniq README.md /tmp/x",
            "tree -o /tmp/x",
            "git remote -v add rogue https://example.com/repo.git",
            "git show --ext-diff HEAD",
            "git --config-env=core.fsmonitor=PROBE status",
            "git -ccore.fsmonitor=probe status",
            "cat README.md > /tmp/x",
        ]:
            with self.subTest(command=command):
                self.assertFalse(APPROVE.is_read_only(command))

    def test_auto_approval_retains_simple_readers(self):
        for command in [
            "cat README.md",
            "rg -n pattern hooks/",
            "ls -la",
            "git status --short",
            "git branch --show-current",
            "head -20 README.md | wc -l",
        ]:
            with self.subTest(command=command):
                self.assertTrue(APPROVE.is_read_only(command))

    def test_prod_exec_cannot_hide_behind_arguments_or_comments(self):
        for command in [
            'kubectl exec -n prod pod -- mysql -e "UPDATE t SET a=1" # staging',
            '/usr/local/bin/kubectl exec -n production pod -- mysql -e "UPDATE t SET a=1"',
            "kubectl exec -n prod pod -- mysql -e \"UPDATE t SET note='staging'\"",
            'kubectl exec -n staging --context production pod -- mysql -e "UPDATE t SET a=1"',
            'kubectl exec -n staging pod -- mysql -e "UPDATE t SET a=1"; '
            'kubectl exec -n prod pod -- mysql -e "UPDATE t SET a=1"',
            "DRY=0 python3 apply.py # staging",
            "DRY=0 python3 apply.py --out /tmp/staging-result.json",
            "DRY=0 python3 apply.py --host localhost",
            'sh -c "/usr/local/bin/kubectl exec -n prod pod -- php artisan migrate"',
        ]:
            with self.subTest(command=command):
                self.assertEqual(2, hook_exit("guard-prod-exec", "Bash", {"command": command}))

    def test_explicit_staging_targets_and_reads_still_pass(self):
        for command in [
            "/usr/local/bin/kubectl exec -n staging-api pod -- php artisan migrate",
            "kubectl exec --namespace=staging pod -- php artisan migrate",
            "DRY=0 python3 apply.py --host staging-db",
            'kubectl exec -n production pod -- mysql -e "SELECT id FROM t LIMIT 1"',
        ]:
            with self.subTest(command=command):
                self.assertEqual(0, hook_exit("guard-prod-exec", "Bash", {"command": command}))

    def test_sql_rejects_cte_writes_and_multiple_statements(self):
        for sql in [
            "WITH ids AS (SELECT 1 AS id) UPDATE t SET a=1 WHERE id IN (SELECT id FROM ids)",
            "SELECT 1; DELETE FROM t WHERE id=1",
            "SELECT 1--1; DELETE FROM t WHERE id=1",
            "WITH ids AS (SELECT 1 AS id) DELETE FROM t WHERE id IN (SELECT id FROM ids)",
            "SELECT id INTO OUTFILE '/tmp/x' FROM t LIMIT 1",
            "SELECT 1 /*!; DELETE FROM t */",
            "CALL mutate_data()",
        ]:
            with self.subTest(sql=sql):
                self.assertEqual(2, hook_exit("guard-prod-sql", "mcp__mysql-prod__mysql_query", {"sql": sql}))

    def test_sql_retains_literals_comments_and_read_ctes(self):
        for sql in [
            "SELECT 'DELETE; UPDATE' AS text;",
            "SELECT id FROM orders WHERE id=1 LIMIT 1",
            "WITH ids AS (SELECT id FROM orders WHERE id=1) SELECT * FROM ids LIMIT 1",
            "SHOW TABLES",
            "SHOW CREATE TABLE orders",
            "SELECT REPLACE('abc', 'a', 'b')",
            "EXPLAIN SELECT * FROM orders WHERE id=1",
            "SELECT COUNT(*) FROM orders -- allow-full-scan",
        ]:
            with self.subTest(sql=sql):
                self.assertEqual(0, hook_exit("guard-prod-sql", "mcp__mysql-prod__mysql_query", {"sql": sql}))

    def test_permission_hook_only_emits_approval_for_readers(self):
        with tempfile.TemporaryDirectory() as home:
            for command, approved in [
                ("cat README.md", True),
                ("sort -o /tmp/x README.md", False),
                ("git -ccore.fsmonitor=probe status", False),
            ]:
                with self.subTest(command=command):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "hooks/auto-approve-readonly.py")],
                        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
                        env={**os.environ, "HOME": home},
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    if approved:
                        output = json.loads(result.stdout)
                        self.assertEqual("allow", output["hookSpecificOutput"]["decision"]["behavior"])
                    else:
                        self.assertEqual("", result.stdout)

    def test_checker_discovers_this_clone_with_an_empty_home(self):
        with tempfile.TemporaryDirectory() as directory:
            for skill, code in [("writing-unslop", 0), ("missing-skill", 2)]:
                with self.subTest(skill=skill):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "scripts/check-skills.py"), "--repo", directory, "--only", skill],
                        env={**os.environ, "HOME": directory},
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(code, result.returncode, result.stderr)
                    if not code:
                        self.assertIn("1 skills checked", result.stdout)

    def test_printed_snapshot_requires_a_successful_bash_result_and_nonempty_file(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "ticket-before-20261008.json"
            transcript = Path(directory) / "transcript.jsonl"
            for tool, error, contents, prefix, expected in [
                ("Bash", False, "[]", "snapshot: ", True),
                ("Bash", True, "[]", "snapshot: ", False),
                ("Read", False, "[]", "snapshot: ", False),
                ("Bash", False, "", "snapshot: ", False),
                ("Bash", False, "[]", "", False),
            ]:
                with self.subTest(tool=tool, error=error, contents=contents, prefix=prefix):
                    snapshot.write_text(contents)
                    entries = [
                        {
                            "type": "assistant",
                            "message": {"content": [{"type": "tool_use", "id": "dry-run", "name": tool, "input": {}}]},
                        },
                        {
                            "type": "user",
                            "message": {
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": "dry-run",
                                        "is_error": error,
                                        "content": prefix + str(snapshot),
                                    }
                                ]
                            },
                        },
                    ]
                    transcript.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
                    self.assertEqual(expected, EXEC.session_has_snapshot(str(transcript)))

    def test_snapshot_requires_successful_creation_and_nonempty_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "ticket-before-20261008.json"
            transcript = Path(directory) / "transcript.jsonl"
            snapshot.write_text('[{"id": 1}]')
            for name, inp, error, result, exists, nonempty, expected in [
                ("Bash", {"command": f"cat {snapshot}"}, False, True, True, True, False),
                ("Bash", {"command": f"echo data > {snapshot}"}, False, True, True, True, True),
                ("Write", {"file_path": str(snapshot)}, True, True, True, True, False),
                ("Write", {"file_path": str(snapshot)}, False, False, True, True, False),
                ("Write", {"file_path": str(snapshot)}, False, True, False, True, False),
                ("Write", {"file_path": str(snapshot)}, False, True, True, False, False),
                ("Write", {"file_path": str(snapshot)}, False, True, True, True, True),
            ]:
                with self.subTest(name=name, inp=inp, error=error, result=result, exists=exists, nonempty=nonempty):
                    snapshot.unlink(missing_ok=True)
                    if exists:
                        snapshot.write_text('[{"id": 1}]' if nonempty else "")
                    entries = [
                        {
                            "type": "assistant",
                            "message": {"content": [{"type": "tool_use", "id": "snapshot", "name": name, "input": inp}]},
                        }
                    ]
                    if result:
                        entries.append(
                            {
                                "type": "user",
                                "message": {
                                    "content": [
                                        {"type": "tool_result", "tool_use_id": "snapshot", "is_error": error, "content": "done"}
                                    ]
                                },
                            }
                        )
                    transcript.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
                    self.assertEqual(expected, EXEC.session_has_snapshot(str(transcript)))


if __name__ == "__main__":
    unittest.main()
