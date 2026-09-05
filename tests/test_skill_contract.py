import json
import re
import shlex
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README_FILE = ROOT / "README.md"
SKILL_DIR = ROOT / "knowledge-distiller"
SKILL_FILE = SKILL_DIR / "SKILL.md"
EVAL_FILE = SKILL_DIR / "evals" / "evals.json"
ADAPTER_COMPATIBILITY_FILE = SKILL_DIR / "references" / "adapter-compatibility.md"
ADAPTER_CONTRACT_FILE = SKILL_DIR / "references" / "adapter-contract.md"


class SkillContractTest(unittest.TestCase):
    def markdown_table(self, text: str, header_prefix: str) -> tuple[list[str], list[dict[str, str]]]:
        lines = text.splitlines()
        start = next(
            (
                index
                for index, line in enumerate(lines)
                if line.startswith("|") and line.strip().startswith(header_prefix)
            ),
            None,
        )
        self.assertIsNotNone(start, f"missing Markdown table: {header_prefix}")
        table_lines = []
        for line in lines[start:]:
            if not line.startswith("|"):
                break
            table_lines.append(line)
        self.assertGreaterEqual(len(table_lines), 3)
        headers = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
        rows = []
        for line in table_lines[2:]:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            self.assertEqual(len(cells), len(headers), line)
            rows.append(dict(zip(headers, cells)))
        return headers, rows

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
        for reference in (
            "workflow.md",
            "authorization.md",
            "artifact-policy.md",
            "adapter-compatibility.md",
            "adapter-contract.md",
        ):
            self.assertIn(f"references/{reference}", text)
            self.assertTrue((SKILL_DIR / "references" / reference).is_file())

    def test_adapter_references_define_an_explicitly_blocked_gate(self) -> None:
        self.assertTrue(
            ADAPTER_COMPATIBILITY_FILE.is_file(),
            "references/adapter-compatibility.md must exist",
        )
        self.assertTrue(
            ADAPTER_CONTRACT_FILE.is_file(),
            "references/adapter-contract.md must exist",
        )
        text = ADAPTER_COMPATIBILITY_FILE.read_text(encoding="utf-8")
        headers, rows = self.markdown_table(text, "| Adapter |")
        self.assertEqual(
            headers,
            [
                "Adapter",
                "Readiness",
                "Locally observed client",
                "Product capability evidence",
                "Stable parse-contract evidence",
            ],
        )
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            {row["Adapter"] for row in rows},
            {"Lark", "Codex", "Claude Code", "Trae"},
        )
        self.assertEqual(len({row["Adapter"] for row in rows}), 4)
        self.assertEqual({row["Readiness"] for row in rows}, {"`blocked`"})
        for row in rows:
            self.assertTrue(row["Locally observed client"])
            self.assertTrue(row["Product capability evidence"])
            self.assertTrue(row["Stable parse-contract evidence"])
        lower = text.lower()
        self.assertIn("retrieved: 2026-09-04", lower)
        self.assertIn("no native product or schema version is supported yet", lower)
        self.assertEqual(
            re.findall(r"(?mi)^supported native versions:\s*(.+)$", text),
            ["none."],
        )
        self.assertIn("no real source was inspected", lower)
        self.assertNotRegex(lower, r"readiness[^\n]*\| `(?:ready|supported|experimental)`")
        for adapter in ("Lark", "Codex", "Claude Code", "Trae"):
            section = text.split(f"## {adapter}\n", 1)[1].split("\n## ", 1)[0]
            for label in (
                "Official evidence",
                "Discovery/read interface",
                "Principal binding",
                "Revision/append semantics",
                "Missing guarantee",
                "Fixtures required to unblock",
                "Failure behavior",
            ):
                self.assertIn(f"**{label}:**", section)

        rows_by_adapter = {row["Adapter"]: row for row in rows}
        self.assertEqual(
            rows_by_adapter["Trae"]["Locally observed client"],
            "`traecli 0.202.3(internal edition)`",
        )
        self.assertIn("TraeCode CLI", text)
        self.assertIn("Trae Agent", text)
        self.assertNotIn("successful envelopes include", lower)
        normalized = " ".join(text.split())
        self.assertIn("The executable is `trae-cli`.", normalized)
        self.assertIn(
            "`traecli` without the hyphen is an unrelated Coco executable and "
            "was not used as evidence.",
            normalized,
        )
        self.assertIn(
            "Resume appends to the selected session; fork creates a new session "
            "that copies the selected history.",
            normalized,
        )

    def test_adapter_contract_distinguishes_root_and_nullable_stream_ids(self) -> None:
        text = ADAPTER_CONTRACT_FILE.read_text(encoding="utf-8")
        _, rows = self.markdown_table(text, "| Event field |")
        by_field = {row["Event field"].strip("`"): row for row in rows}
        self.assertEqual(
            set(by_field),
            {
                "id",
                "native_event_id",
                "native_offset",
                "source_snapshot_id",
                "root_session_id",
                "session_id",
                "thread_id",
                "root_stream_id",
                "stream_id",
                "branch_id",
                "parent_event_id",
                "span_id",
                "event_type",
                "actor",
                "project_workspace",
                "native_timestamp",
                "stream_position",
                "correlation_id",
                "chunk_index",
                "content_segments",
                "artifact_locators",
                "observable_outcome",
                "semantic_markers",
                "claim_eligible",
            },
        )
        self.assertTrue(all(row["Required"] == "yes" for row in rows))
        self.assertEqual(by_field["root_stream_id"]["Nullable"], "no")
        for field in (
            "root_session_id",
            "session_id",
            "thread_id",
            "stream_id",
            "branch_id",
            "parent_event_id",
            "span_id",
        ):
            self.assertEqual(by_field[field]["Type"], "string")
            self.assertEqual(by_field[field]["Nullable"], "yes")
        self.assertEqual(
            by_field["chunk_index"]["Constraints"],
            "0..2147483647 when non-null; conditional rule below",
        )

        normalized = " ".join(text.split())
        self.assertIn(
            "`stream_position` defines order within each native stream.",
            normalized,
        )
        self.assertIn(
            "The `events` array order has no chronological meaning.",
            normalized,
        )

    def test_adapter_contract_defines_closed_machine_implementable_schemas(self) -> None:
        text = ADAPTER_CONTRACT_FILE.read_text(encoding="utf-8")
        expected_fields = {
            "Top-level field": {
                "schema_version",
                "adapter",
                "source_snapshot_id",
                "owner",
                "events",
                "edges",
                "fidelity_losses",
            },
            "Adapter field": {
                "name",
                "adapter_version",
                "product_version",
                "native_schema_version",
            },
            "Owner field": {"kind", "id", "verification"},
            "Actor field": {"kind", "id", "resolution"},
            "Workspace field": {"kind", "id"},
            "Segment field": {"type", "text", "media_type", "artifact_locator_id"},
            "Artifact field": {"id", "kind", "locator", "revision"},
            "Outcome field": {"kind", "status", "summary"},
            "Edge field": {"id", "edge_type", "from", "to"},
            "Reference field": {"status", "event_id", "source_snapshot_id", "reason"},
            "Fidelity-loss field": {"code", "event_id", "native_fact", "reason"},
        }
        for header, fields in expected_fields.items():
            _, rows = self.markdown_table(text, f"| {header} |")
            self.assertEqual({row[header].strip("`") for row in rows}, fields)
            self.assertTrue(all(row["Required"] == "yes" for row in rows))

        normalized = " ".join(text.split())
        for requirement in (
            "Unknown fields are rejected for the root and every nested object.",
            "Identifier strings are 1..256 UTF-8 bytes.",
            "Each serialized event is at most 1048576 bytes",
            "The complete canonical graph is at most 67108864 bytes.",
            "`json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(\",\", \":\"))`",
            "`sha256:` followed by the 64 lowercase hexadecimal digits",
            "A non-null `native_event_id` is unique across the entire snapshot.",
            "`claim_eligible` is true if and only if `actor.kind` is `user`, `actor.resolution` is `verified-owner`, and `actor.id == owner.id`.",
            "Raw JSON must pass through `decode_event_graph_json` before `validate_event_graph`",
            "an immutable `ValidationContext`",
            "Every local edge whose endpoints have the same native stream key",
            "Every child start has exactly one matching terminal",
            "`(root_stream_id, actor.id, correlation_id)`",
            "`parent_event_id` need not equal the child start ID",
        ):
            self.assertIn(requirement, normalized)

    def test_adapter_contract_defines_json_parser_resource_budgets(self) -> None:
        normalized = " ".join(
            ADAPTER_CONTRACT_FILE.read_text(encoding="utf-8").split()
        )
        for requirement in (
            "750000 total lexical tokens",
            "500000 structural tokens",
            "250000 value tokens",
            "200000 string tokens",
            "64 nesting levels",
            "returns only the total token count",
            "getsizeof(raw) + 2 * getsizeof(decoded_text) + MAX_GRAPH_BYTES + 384 * total_tokens <= 512 MiB",
            "aggregate copy of materialized scalar payloads",
            "reserves the canonical output buffer",
            "permits non-ASCII text when the total remains below the ceiling",
        ):
            self.assertIn(requirement, normalized)

    def test_adapter_contract_accepts_only_one_synthetic_tuple(self) -> None:
        text = ADAPTER_CONTRACT_FILE.read_text(encoding="utf-8")
        headers, rows = self.markdown_table(text, "| Adapter name |")
        self.assertEqual(
            headers,
            [
                "Adapter name",
                "adapter_version",
                "product_version",
                "native_schema_version",
            ],
        )
        self.assertEqual(
            rows,
            [
                {
                    "Adapter name": "`synthetic`",
                    "adapter_version": "`1.0.0`",
                    "product_version": "`synthetic-1`",
                    "native_schema_version": "`synthetic-1`",
                },
                {
                    "Adapter name": "`lark`",
                    "adapter_version": "none",
                    "product_version": "none",
                    "native_schema_version": "none",
                },
                {
                    "Adapter name": "`codex`",
                    "adapter_version": "none",
                    "product_version": "none",
                    "native_schema_version": "none",
                },
                {
                    "Adapter name": "`claude-code`",
                    "adapter_version": "none",
                    "product_version": "none",
                    "native_schema_version": "none",
                },
                {
                    "Adapter name": "`trae`",
                    "adapter_version": "none",
                    "product_version": "none",
                    "native_schema_version": "none",
                },
            ],
        )
        self.assertIn(
            "Any other adapter/version tuple is rejected.",
            " ".join(text.split()),
        )

    def test_every_edge_type_has_closed_forward_semantics(self) -> None:
        text = ADAPTER_CONTRACT_FILE.read_text(encoding="utf-8")
        headers, rows = self.markdown_table(text, "| Edge type |")
        self.assertEqual(
            headers,
            [
                "Edge type",
                "Direction",
                "Allowed from event_type",
                "Allowed to event_type",
                "Cardinality",
            ],
        )
        self.assertEqual(
            {row["Edge type"].strip("`") for row in rows},
            {
                "precedes",
                "tool-output-of",
                "tool-result-of",
                "edits",
                "retries",
                "supersedes",
                "forks",
                "spawned-by",
                "sent",
                "delivered",
                "consumed",
                "joined",
                "returned",
                "cancellation-observed",
            },
        )
        self.assertEqual(len(rows), 14)
        for row in rows:
            self.assertEqual(row["Direction"], "predecessor -> dependent")
            self.assertTrue(row["Allowed from event_type"])
            self.assertTrue(row["Allowed to event_type"])
            self.assertTrue(row["Cardinality"])
        self.assertIn(
            "Edge names do not reverse this direction",
            " ".join(text.split()),
        )

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

    def test_skill_routes_compatibility_questions_without_graph_validation(self) -> None:
        paragraphs = [" ".join(part.split()) for part in self.skill_text().split("\n\n")]
        routes = [
            paragraph
            for paragraph in paragraphs
            if "adapter compatibility questions" in paragraph.lower()
        ]
        self.assertEqual(len(routes), 1)
        route = routes[0]
        self.assertIn("references/adapter-compatibility.md", route)
        self.assertNotIn("adapter-contract.md", route)
        self.assertNotIn("validate-event-graph", route)
        self.assertNotIn("candidate canonical graph", route.lower())

    def test_skill_routes_candidate_graphs_after_external_anchors(self) -> None:
        paragraphs = [" ".join(part.split()) for part in self.skill_text().split("\n\n")]
        routes = [
            paragraph
            for paragraph in paragraphs
            if "candidate canonical graph" in paragraph.lower()
        ]
        self.assertEqual(len(routes), 1)
        route = routes[0].lower()
        self.assertIn("externally established", route)
        self.assertIn("owner", route)
        self.assertIn("source snapshot", route)
        self.assertIn("references/adapter-contract.md", route)
        self.assertIn("validate-event-graph", route)
        self.assertNotIn("adapter-compatibility.md", route)

    def test_public_guidance_routes_canonical_graphs_through_the_bounded_gate(self) -> None:
        guidance = {
            "SKILL.md": (
                self.skill_text(),
                ["python3", "scripts/kd.py"],
            ),
            "README.md": (
                README_FILE.read_text(encoding="utf-8"),
                ["python3", "knowledge-distiller/scripts/kd.py"],
            ),
        }
        snapshot_id = "sha256:" + "a" * 64
        for name, (text, prefix) in guidance.items():
            with self.subTest(file=name):
                self.assertIn("adapter-compatibility.md", text)
                self.assertIn("adapter-contract.md", text)
                commands = [
                    line.strip()
                    for line in text.splitlines()
                    if line.strip().startswith("python3 ")
                    and " validate-event-graph " in line
                ]
                self.assertEqual(len(commands), 1)
                command = commands[0]
                self.assertNotRegex(command, r"[<>]")
                arguments = shlex.split(command)
                self.assertEqual(
                    arguments,
                    prefix
                    + [
                        "validate-event-graph",
                        "/absolute/path/to/graph.json",
                        "--expected-owner-id",
                        "OWNER_ID",
                        "--expected-source-snapshot-id",
                        snapshot_id,
                    ],
                )
                snapshot_index = arguments.index("--expected-source-snapshot-id") + 1
                self.assertRegex(arguments[snapshot_index], r"\Asha256:[0-9a-f]{64}\Z")

    def test_public_guidance_limits_what_event_graph_validation_proves(self) -> None:
        for name, path in (
            ("SKILL.md", SKILL_FILE),
            ("README.md", README_FILE),
        ):
            text = " ".join(path.read_text(encoding="utf-8").lower().split())
            with self.subTest(file=name):
                self.assertIn(
                    "contract and synthetic conformance harness exist",
                    text,
                )
                self.assertIn(
                    "lark, codex, claude code, and trae native adapters remain "
                    "blocked and unimplemented",
                    text,
                )
                self.assertIn(
                    "validation proves only that the graph conforms to the listed "
                    "synthetic tuple and canonical contract",
                    text,
                )
                self.assertIn("does not authorize a source read", text)
                self.assertIn("does not authorize native tool invocation", text)
                self.assertIn("does not make any native adapter supported", text)

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
