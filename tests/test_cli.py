import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "knowledge-distiller" / "scripts" / "kd.py"
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller.persistence import TaskCoordinator, create_task  # noqa: E402


class CliTest(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI), *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_validate_draft_emits_json_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "draft"
            draft.mkdir()
            (draft / "SKILL.md").write_text(
                "---\nname: guide\ndescription: Use when investigating.\n---\n\n# Guide\n",
                encoding="utf-8",
            )

            result = self.run_cli("validate-draft", str(draft))

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual([item["path"] for item in payload["manifest"]], ["SKILL.md"])
        self.assertEqual(result.stderr, "")

    def test_policy_rejection_has_stable_exit_and_does_not_leak_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "draft"
            draft.mkdir()
            outside = Path(directory) / "outside.md"
            outside.write_text("PRIVATE-SOURCE-CONTENT", encoding="utf-8")
            (draft / "SKILL.md").symlink_to(outside)

            result = self.run_cli("validate-draft", str(draft))

        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "draft-rejected")
        self.assertEqual(payload["error"]["reason"], "symlink")
        self.assertNotIn("PRIVATE-SOURCE-CONTENT", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_transition_emits_new_immutable_state(self) -> None:
        result = self.run_cli(
            "transition",
            "--state",
            '{"phase":"init","epoch":0}',
            "--event",
            "start-discover",
            "--facts",
            '{"has_seed":true}',
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["state"],
            {
                "epoch": 0,
                "mode": "discover",
                "phase": "scout",
                "prior_phase": None,
            },
        )

    def test_rejected_transition_uses_exit_three(self) -> None:
        result = self.run_cli(
            "transition",
            "--state",
            '{"phase":"init"}',
            "--event",
            "start-discover",
            "--facts",
            "{}",
        )

        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "invalid-transition")
        self.assertIn("has_seed", payload["error"]["message"])

    def test_invalid_json_uses_exit_two_without_echoing_input(self) -> None:
        secret = "PRIVATE-SOURCE-CONTENT"
        result = self.run_cli(
            "transition",
            "--state",
            "not-json-" + secret,
            "--event",
            "start-discover",
            "--facts",
            "{}",
        )

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "invalid-input")
        self.assertEqual(payload["error"]["reason"], "invalid-json")
        self.assertNotIn(secret, result.stderr)

    def test_cli_does_not_accept_user_supplied_asset_trust(self) -> None:
        image_bytes = b"\x89PNG\r\n\x1a\n" + b"unreviewed"
        digest = hashlib.sha256(image_bytes).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "draft"
            assets = draft / "assets"
            assets.mkdir(parents=True)
            (draft / "SKILL.md").write_text(
                "---\nname: guide\ndescription: Use when investigating.\n---\n",
                encoding="utf-8",
            )
            (assets / "image.png").write_bytes(image_bytes)

            result = self.run_cli(
                "validate-draft",
                str(draft),
                "--allowed-asset-sha256",
                digest,
            )

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["reason"], "invalid-arguments")

    def test_imported_state_cannot_bypass_workflow_topology(self) -> None:
        impossible_states = (
            ('{"phase":"compile"}', "draft-compiled", '{"draft_valid":true}'),
            (
                '{"mode":"distill","phase":"suspended","prior_phase":"done"}',
                "resume",
                '{"resume_valid":true}',
            ),
        )
        for state, event, facts in impossible_states:
            with self.subTest(state=state):
                result = self.run_cli(
                    "transition",
                    "--state",
                    state,
                    "--event",
                    event,
                    "--facts",
                    facts,
                )
                self.assertEqual(result.returncode, 2)
                payload = json.loads(result.stderr)
                self.assertEqual(payload["error"]["reason"], "invalid-state")

    def test_auth_restore_rejects_target_incompatible_with_mode(self) -> None:
        result = self.run_cli(
            "transition",
            "--state",
            '{"mode":"update","phase":"auth-stale","prior_phase":"source-review"}',
            "--event",
            "auth-restored",
            "--facts",
            (
                '{"auth_restored":true,"purge_complete":true,'
                '"revision_target":"map"}'
            ),
        )

        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "invalid-transition")
        self.assertNotIn("Traceback", result.stderr)

    def test_task_init_transition_and_inspect_use_durable_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task"
            initialized = self.run_cli("task-init", str(task))
            advanced = self.run_cli(
                "task-transition",
                str(task),
                "--event",
                "start-discover",
                "--facts",
                '{"has_seed":true}',
            )
            inspected = self.run_cli("task-inspect", str(task))

        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.assertEqual(advanced.returncode, 0, advanced.stderr)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        initial_payload = json.loads(initialized.stdout)["task"]
        advanced_payload = json.loads(advanced.stdout)["task"]
        inspected_payload = json.loads(inspected.stdout)["task"]
        self.assertEqual(initial_payload["state"]["phase"], "init")
        self.assertEqual(advanced_payload["state"]["phase"], "scout")
        self.assertEqual(advanced_payload, inspected_payload)
        self.assertGreater(
            advanced_payload["fencing_epoch"], initial_payload["fencing_epoch"]
        )

    def test_task_init_rejects_nonempty_destination_without_leaking_path_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = "PRIVATE-SOURCE-CONTENT"
            task = Path(directory) / ("task-" + secret)
            task.mkdir()
            (task / "keep").write_text("unchanged", encoding="utf-8")

            result = self.run_cli("task-init", str(task))

        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "task-persistence-error")
        self.assertEqual(payload["error"]["reason"], "workspace-not-empty")
        self.assertNotIn(secret, result.stderr)

    def test_task_transition_rejects_unknown_fact_without_echoing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task"
            create_task(task)
            secret = "PRIVATE-SOURCE-CONTENT"

            result = self.run_cli(
                "task-transition",
                str(task),
                "--event",
                "start-discover",
                "--facts",
                json.dumps({"unknown_" + secret: True}),
            )

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "invalid-input")
        self.assertEqual(payload["error"]["reason"], "unknown-fact-field")
        self.assertNotIn(secret, result.stderr)

    def test_busy_task_has_stable_redacted_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task"
            create_task(task)

            with TaskCoordinator(task):
                result = self.run_cli("task-inspect", str(task), "--recover")

        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "task-busy")
        self.assertEqual(payload["error"]["reason"], "task-busy")


if __name__ == "__main__":
    unittest.main()
