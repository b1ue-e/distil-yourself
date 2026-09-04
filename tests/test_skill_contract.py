import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "knowledge-distiller"
SKILL_FILE = SKILL_DIR / "SKILL.md"
EVAL_FILE = SKILL_DIR / "evals" / "evals.json"


class SkillContractTest(unittest.TestCase):
    def skill_text(self) -> str:
        self.assertTrue(SKILL_FILE.is_file(), "knowledge-distiller/SKILL.md must exist")
        return SKILL_FILE.read_text(encoding="utf-8")

    def test_frontmatter_is_discoverable(self) -> None:
        text = self.skill_text()
        match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(match, "SKILL.md must start with YAML frontmatter")
        frontmatter = match.group(1)
        self.assertIn("name: knowledge-distiller", frontmatter)
        description = next(
            line.removeprefix("description: ")
            for line in frontmatter.splitlines()
            if line.startswith("description: ")
        )
        self.assertTrue(description.startswith("Use when "))
        self.assertNotIn(" I ", f" {description} ")

    def test_body_is_compact_and_routes_to_required_references(self) -> None:
        text = self.skill_text()
        self.assertLess(len(text.splitlines()), 500)
        for reference in ("workflow.md", "authorization.md", "artifact-policy.md"):
            self.assertIn(f"references/{reference}", text)
            self.assertTrue((SKILL_DIR / "references" / reference).is_file())

    def test_body_preserves_foundation_safety_boundary(self) -> None:
        text = self.skill_text().lower()
        for required in (
            "do not read source content",
            "do not install",
            "ask only",
            "not implemented",
        ):
            self.assertIn(required, text)
        for forbidden in ("automatically install", "scan all accessible", "follow every link"):
            self.assertNotIn(forbidden, text)

    def test_body_routes_state_changes_through_durable_checkpoint_commands(self) -> None:
        text = self.skill_text().lower()
        for command in ("task-init", "task-transition", "task-inspect"):
            self.assertIn(command, text)
        self.assertNotIn("persistent checkpoints, sealed evaluation", text)

    def test_checkpoint_guidance_does_not_expand_authority(self) -> None:
        text = self.skill_text().lower()
        self.assertIn("does not grant source access", text)
        self.assertIn("does not authorize external mutation", text)
        self.assertIn("do not place source content", text)

    def test_eval_set_covers_two_triggers_and_one_near_miss(self) -> None:
        self.assertTrue(EVAL_FILE.is_file(), "evals/evals.json must exist")
        payload = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
        self.assertEqual(payload["skill_name"], "knowledge-distiller")
        self.assertEqual([item["id"] for item in payload["evals"]], [1, 2, 3])
        self.assertIn("does not trigger", payload["evals"][2]["expected_output"].lower())


if __name__ == "__main__":
    unittest.main()
