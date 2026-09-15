import json
import re
import shlex
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README_FILE = ROOT / "README.md"
DESIGN_FILE = ROOT / "docs" / "specs" / "knowledge-distiller-design.md"
LARK_RUNTIME_DESIGN_FILE = (
    ROOT / "docs" / "specs" / "2026-09-11-local-lark-runtime-design.md"
)
SKILL_DIR = ROOT / "knowledge-distiller"
SKILL_FILE = SKILL_DIR / "SKILL.md"
EVAL_FILE = SKILL_DIR / "evals" / "evals.json"
ADAPTER_COMPATIBILITY_FILE = SKILL_DIR / "references" / "adapter-compatibility.md"
ADAPTER_CONTRACT_FILE = SKILL_DIR / "references" / "adapter-contract.md"
WORKFLOW_FILE = SKILL_DIR / "references" / "workflow.md"
AUTHORIZATION_FILE = SKILL_DIR / "references" / "authorization.md"
ARTIFACT_POLICY_FILE = SKILL_DIR / "references" / "artifact-policy.md"
KNOWLEDGE_PACKET_FILE = SKILL_DIR / "references" / "knowledge-packet.md"
STATUS_FILE = ROOT / "docs" / "status" / "2026-09-05-implementation-status.md"
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from knowledge_distiller import knowledge  # noqa: E402


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
            "knowledge-packet.md",
        ):
            self.assertIn(f"references/{reference}", text)
            self.assertTrue((SKILL_DIR / "references" / reference).is_file())
        self.assertLess(len(text.split()), 900)

    def test_adapter_references_define_narrow_lark_and_codex_gates(self) -> None:
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
        rows_by_adapter = {row["Adapter"]: row for row in rows}
        self.assertEqual(rows_by_adapter["Lark"]["Readiness"], "`normalizer-supported`")
        self.assertEqual(rows_by_adapter["Codex"]["Readiness"], "`supported`")
        self.assertEqual({rows_by_adapter[name]["Readiness"] for name in
                          ("Claude Code", "Trae")}, {"`blocked`"})
        for row in rows:
            self.assertTrue(row["Locally observed client"])
            self.assertTrue(row["Product capability evidence"])
            self.assertTrue(row["Stable parse-contract evidence"])
        lower = text.lower()
        self.assertIn("retrieved: 2026-09-07", lower)
        self.assertIn(
            "one narrow lark raw-content normalizer and one codex rollout adapter are supported",
            " ".join(lower.split()),
        )
        self.assertIn("dependency-injected ingestion", lower)
        self.assertNotIn("remain task 7", lower)
        self.assertNotIn("remains task 7", lower)
        self.assertEqual(
            re.findall(r"(?mi)^supported native versions:\s*(.+)$", text),
            ["Lark / 1.0.0 / 1.0.86 / docx-v1-raw-content-v1 (normalizer only); Codex / 1.0.0 / 0.153.0 / rollout-jsonl-v1."],
        )
        self.assertIn("no observed content, path, session id, or tenant locator", " ".join(lower.split()))
        self.assertNotRegex(lower, r"readiness[^\n]*\| `(?:ready|experimental)`")
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

    def test_adapter_contract_accepts_only_exact_event_tuples(self) -> None:
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
                    "adapter_version": "`1.0.0`",
                    "product_version": "`0.153.0`",
                    "native_schema_version": "`rollout-jsonl-v1`",
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
        document_headers, document_rows = self.markdown_table(
            text, "| Document adapter |")
        self.assertEqual(
            document_headers,
            ["Document adapter", "adapter_version", "product_version",
             "native_schema_version", "Scope"],
        )
        self.assertEqual(document_rows, [
            {
                "Document adapter": "`synthetic`",
                "adapter_version": "`1.0.0`",
                "product_version": "`synthetic-1`",
                "native_schema_version": "`synthetic-1`",
                "Scope": "conformance fixtures",
            },
            {
                "Document adapter": "`lark`",
                "adapter_version": "`1.0.0`",
                "product_version": "`1.0.86`",
                "native_schema_version": "`docx-v1-raw-content-v1`",
                "Scope": "pure raw-content normalizer only",
            },
        ])
        self.assertIn("does not enable event graphs, source reads, credential handling",
                      " ".join(text.split()).lower())
        self.assertIn(
            "Any other adapter/version tuple is rejected by the event-graph validator.",
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
            "ask at most",
            "unavailable",
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
                    "one exact lark raw-content normalizer tuple and one exact codex "
                    "rollout adapter tuple are available",
                    text,
                )
                self.assertIn("dependency-injected", text)
                self.assertIn("dual-source ingestion", text)
                self.assertIn("production lark runtime", text)
                self.assertIn("production codex runtime", text)
                self.assertIn("claude code adapter", text)
                self.assertIn("trae adapter", text)
                self.assertIn(
                    "validation proves only that the graph conforms to an allowlisted "
                    "exact tuple and the canonical contract",
                    text,
                )
                self.assertIn("does not authorize a source read", text)
                self.assertIn("native tool invocation", text)
                self.assertIn("directory enumeration", text)
                self.assertIn("native readiness is defined separately", text)

    def test_readme_documents_cli_exit_code_categories(self) -> None:
        text = " ".join(README_FILE.read_text(encoding="utf-8").split())
        self.assertIn(
            "Input-boundary failures, including encoding, JSON, resource-limit, "
            "and unsafe-source errors, exit with code `2`.",
            text,
        )
        self.assertIn(
            "Duplicate JSON keys, canonical event-graph contract rejections, "
            "rejected artifacts, and rejected transitions exit with code `3`",
            text,
        )

    def test_checkpoint_guidance_does_not_expand_authority(self) -> None:
        text = self.skill_text().lower()
        self.assertIn("does not grant source access", text)
        self.assertIn("does not authorize external mutation", text)
        self.assertIn("do not place source content", text)

    def test_public_guidance_exposes_the_exact_supported_core_commands(self) -> None:
        commands = (
            "ingest-source /absolute/path/to/task-workspace /absolute/path/to/lark-request.json",
            "ingest-source /absolute/path/to/task-workspace /absolute/path/to/codex-request.json",
            "validate-knowledge-packet /absolute/path/to/knowledge-packet.json",
            "next-critical-question /absolute/path/to/knowledge-packet.json",
            "adjudicate-knowledge-packet /absolute/path/to/task-workspace /absolute/path/to/knowledge-packet.json --transaction-id DECISION_ID --expected-generation-id GENERATION_ID",
            "compile-capability /absolute/path/to/task-workspace /absolute/path/to/knowledge-packet.json --transaction-id COMPILE_ID --expected-generation-id GENERATION_ID",
        )
        for name, path, prefix in (
            ("workflow.md", WORKFLOW_FILE, "python3 scripts/kd.py "),
            ("README.md", README_FILE,
             "python3 knowledge-distiller/scripts/kd.py "),
        ):
            lines = {" ".join(line.split()) for line in
                     path.read_text(encoding="utf-8").splitlines()}
            with self.subTest(file=name):
                for command in commands:
                    self.assertIn(prefix + command, lines)

    def test_public_guidance_defines_the_standalone_codex_runtime_boundary(self) -> None:
        texts = {
            path.name: path.read_text(encoding="utf-8")
            for path in (
                SKILL_FILE,
                WORKFLOW_FILE,
                AUTHORIZATION_FILE,
                README_FILE,
            )
        }
        normalized = " ".join(" ".join(texts.values()).lower().split())
        for requirement in (
            "ingest-codex-session",
            "--redaction-key-file",
            "knowledge-distiller.local-codex-ingestion-request/v1",
            "one explicit session file",
            "exact byte-0 prefix",
            "effective uid",
            "0600",
            "does not authorize a real session read",
            "ingest-source",
            "ingestion-runtime-unavailable",
            "codex / 1.0.0 / 0.153.0 / rollout-jsonl-v1",
            "caller never supplies uid, owner, issuer, grant, attestation, adapter version, or native schema",
            "filesystem selector is persisted only as a domain-separated commitment",
            "opaque `project_id` is required for private provenance binding but is never printed",
            "synthetic acceptance does not authorize any real session read",
            "discovery, sibling reads, arbitrary versions, lark standalone access, automatic extraction, evaluation, export, installation, and publication are unavailable",
            "key generation and lifecycle are outside this milestone",
        ):
            self.assertIn(requirement, normalized)

        command = (
            "python3 knowledge-distiller/scripts/kd.py ingest-codex-session \\\n"
            "  /absolute/path/to/task-workspace \\\n"
            "  /absolute/path/to/private-codex-request.json \\\n"
            "  --redaction-key-file /absolute/path/to/redaction.key"
        )
        self.assertIn(command, texts["README.md"])
        self.assertIn(command, texts["workflow.md"])
        for overclaim in (
            "automatically discovers Codex sessions",
            "supports every Codex version",
            "creates the redaction key",
            "installs the distilled skill",
            "publishes the distilled skill",
        ):
            self.assertNotIn(overclaim.lower(), normalized)

    def test_public_guidance_defines_the_synthetic_lark_runtime_boundary(self) -> None:
        command = (
            "python3 knowledge-distiller/scripts/kd.py ingest-lark-document "
            "TASK_PATH REQUEST.json --redaction-key-file KEY --allow-live-read"
        )
        design_link = "docs/specs/2026-09-11-local-lark-runtime-design.md"
        files = {
            "README.md": README_FILE,
            "SKILL.md": SKILL_FILE,
            "workflow.md": WORKFLOW_FILE,
            "authorization.md": AUTHORIZATION_FILE,
            "adapter-compatibility.md": ADAPTER_COMPATIBILITY_FILE,
            "status.md": STATUS_FILE,
            "design.md": LARK_RUNTIME_DESIGN_FILE,
        }
        normalized = {
            name: " ".join(path.read_text(encoding="utf-8").split())
            for name, path in files.items()
        }

        for name, link in (
            ("README.md", design_link),
            ("SKILL.md", "../" + design_link),
        ):
            with self.subTest(file=name, contract="single-authority"):
                self.assertIn(link, normalized[name])
                self.assertIn("canonical and single authority", normalized[name])
                self.assertIn("synthetic-only", normalized[name])
                self.assertIn("does not authorize a real Lark read", normalized[name])

        workflow = normalized["workflow.md"]
        self.assertIn(command, workflow)
        self.assertIn("exactly these five fields", workflow)
        for field in (
            "schema_version", "transaction_id", "expected_generation_id",
            "document_selector", "derived_processing_until",
        ):
            self.assertIn(f"`{field}`", workflow)
        for requirement in (
            "exact 27-character ASCII alphanumeric token",
            "Wiki URLs are unsupported",
            "reverify the same `openId`",
            "before/after `LarkObservation`",
            "not an atomic or cryptographic snapshot",
            "lark-live-disabled",
        ):
            self.assertIn(requirement, workflow)

        authorization = normalized["authorization.md"]
        for requirement in (
            "openId == owner_id",
            "only in memory",
            "opaque SHA-256 commitments",
            "separate from local workspace owner-only permissions",
        ):
            self.assertIn(requirement, authorization)

        adapter = normalized["adapter-compatibility.md"]
        for requirement in (
            "lark / adapter 1.0.0 / lark-cli 1.0.86 / docx-v1-raw-content-v1",
            "observational sandwich",
            "endpoint-integrity evidence",
            "accepted consistency mode",
            "one explicitly approved exact-document probe",
            "Actual CLI response compatibility has not been confirmed",
        ):
            self.assertIn(requirement, adapter)

        status = normalized["status.md"]
        for requirement in (
            "synthetic Lark runtime milestone",
            "endpoint-integrity evidence",
            "accepted consistency mode",
            "one explicitly approved exact-document probe",
            "feature branch: `feat/implement_knowledge_distiller`",
            "work remains local and has not been pushed",
        ):
            self.assertIn(requirement, status)
        self.assertNotRegex(status, r"\bahead\s+\d+\b")
        for stale_value in ("origin still at", "1304572", "a3be2f5"):
            self.assertNotIn(stale_value, status)

        design = normalized["design.md"]
        for requirement in (
            "Status: implemented (synthetic-only)",
            "knowledge-distiller.local-lark-ingestion-request/v1",
            "owner_id", "openId", "observational", "lark-cli 1.0.86",
            "Wiki URLs are unsupported",
            "does not authorize a real Lark read",
            "endpoint-integrity evidence",
            "accepted consistency mode",
            "one separately approved exact-document probe",
        ):
            self.assertIn(requirement, design)

    def test_core_guidance_defines_authority_provenance_and_question_boundaries(self) -> None:
        combined = " ".join((
            self.skill_text(),
            WORKFLOW_FILE.read_text(encoding="utf-8"),
            AUTHORIZATION_FILE.read_text(encoding="utf-8"),
        )).lower()
        normalized = " ".join(combined.split())
        for requirement in (
            "one source selector and pinned revision or closed session range",
            "contentgrant and authorityattestation",
            "before every source read",
            "source snapshot → native evidence → redacted span → contentgrant → authorityattestation",
            "ask at most one critical question at a time",
            "lower-impact uncertainty does not block progress",
            "exact adjudicated packet bytes",
            "explicitly confirm the selected capability and every claim",
            "does not authenticate the current user",
            "do not generate or infer user confirmation",
            "does not install or export",
        ):
            self.assertIn(requirement, normalized)

    def test_workflow_matches_mode_specific_post_ingestion_phases(self) -> None:
        normalized = " ".join(
            WORKFLOW_FILE.read_text(encoding="utf-8").lower().split())
        self.assertIn("`discover` advances to `map`", normalized)
        self.assertIn(
            "`capability_map_ready` then advances to `capability-review`",
            normalized,
        )
        self.assertIn(
            "`distill` and `update` advance directly to `capability-review`",
            normalized,
        )

    def test_design_requires_claim_review_after_every_extraction(self) -> None:
        normalized = " ".join(
            DESIGN_FILE.read_text(encoding="utf-8").lower().split())
        self.assertNotIn(
            "`extract` | evidence predicate passes with no high-impact conflict | `compile`",
            normalized,
        )
        self.assertIn(
            "`extract` | evidence predicate passes; proposed claims and critical "
            "questions are ready for adjudication | `claim_review`",
            normalized,
        )

    def test_ingestion_shell_examples_are_syntax_only(self) -> None:
        for name, path in (
            ("SKILL.md", SKILL_FILE),
            ("workflow.md", WORKFLOW_FILE),
            ("README.md", README_FILE),
        ):
            normalized = " ".join(
                path.read_text(encoding="utf-8").lower().split())
            with self.subTest(file=name):
                self.assertIn("embedded api boundary", normalized)
                self.assertIn("syntax only", normalized)
                self.assertIn("ingestion-runtime-unavailable", normalized)
                self.assertIn("before reading the request file", normalized)

    def test_guidance_names_every_unavailable_boundary(self) -> None:
        for name, path in (("SKILL.md", SKILL_FILE), ("README.md", README_FILE)):
            paragraphs = [" ".join(part.lower().split()) for part in
                          path.read_text(encoding="utf-8").split("\n\n")]
            with self.subTest(file=name):
                unavailable = next(
                    (part for part in paragraphs
                     if "automatic discovery" in part and "unavailable" in part),
                    "",
                )
                self.assertTrue(unavailable)
                for boundary in (
                    "automatic discovery",
                    "production lark runtime",
                    "production codex runtime",
                    "claude code adapter",
                    "trae adapter",
                    "sealed evaluation",
                    "approval signatures",
                    "export",
                    "installation",
                    "publication",
                ):
                    self.assertIn(boundary, unavailable)

    def test_artifact_guidance_matches_the_current_fixed_compiler(self) -> None:
        for name, path in (
            ("README.md", README_FILE),
            ("artifact-policy.md", ARTIFACT_POLICY_FILE),
        ):
            normalized = " ".join(
                path.read_text(encoding="utf-8").lower().split())
            with self.subTest(file=name):
                self.assertIn("fixed two-file", normalized)
                self.assertIn("empty asset allowlist", normalized)
                self.assertNotIn("future compiler may call", normalized)

    def test_knowledge_packet_reference_is_sufficient_and_routed(self) -> None:
        self.assertTrue(KNOWLEDGE_PACKET_FILE.is_file())
        skill = self.skill_text()
        self.assertIn("references/knowledge-packet.md", skill)
        text = KNOWLEDGE_PACKET_FILE.read_text(encoding="utf-8")
        normalized = " ".join(text.lower().split())
        for field in (
            "schema_version", "evidence", "claims", "decisions", "candidates",
            "model", "questions", "uncertainties",
        ):
            self.assertIn(f"`{field}`", text)
        for section in knowledge.SECTIONS:
            self.assertIn(f'"{section}"', text)
        for requirement in (
            "knowledge-distiller.knowledge-packet/v1",
            "behavior or claim question answer must become claimdecision",
            "new-authority-required",
            "trusted broker obtains a new active contentgrant and authorityattestation",
            "never encode missing authority as claimdecision",
            "questions must be empty before adjudication",
            "exact same packet bytes",
            "schema-valid synthetic skeleton",
        ):
            self.assertIn(requirement, normalized)
        skeleton = re.search(
            r"## Schema-valid synthetic skeleton.*?```json\n(.*?)\n```",
            text,
            re.DOTALL,
        )
        self.assertIsNotNone(skeleton)
        knowledge.validate_packet(json.loads(skeleton.group(1)))

    def test_guidance_does_not_overclaim_live_revalidation_or_evidence_review(self) -> None:
        combined = " ".join(
            path.read_text(encoding="utf-8") for path in
            (SKILL_FILE, WORKFLOW_FILE, README_FILE)
        ).lower()
        normalized = " ".join(combined.split())
        self.assertNotIn("revalidates live provenance and grants", normalized)
        self.assertIn(
            "revalidates persisted provenance and recorded grant digest/time bounds",
            normalized,
        )
        self.assertIn("bounded evidence review is unavailable", normalized)
        self.assertIn(
            "an explicit request does not create a supported evidence-output path",
            normalized,
        )

    def test_eval_set_covers_two_triggers_and_one_near_miss(self) -> None:
        self.assertTrue(EVAL_FILE.is_file(), "evals/evals.json must exist")
        payload = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
        self.assertEqual(payload["skill_name"], "knowledge-distiller")
        self.assertEqual([item["id"] for item in payload["evals"]], [1, 2, 3])
        self.assertIn("does not trigger", payload["evals"][2]["expected_output"].lower())


if __name__ == "__main__":
    unittest.main()
