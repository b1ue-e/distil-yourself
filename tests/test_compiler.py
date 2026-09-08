"""Synthetic end-to-end tests for closed capability draft compilation."""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller import artifacts, compiler
from knowledge_distiller.journal import canonical_json
from knowledge_distiller.persistence import TaskCoordinator, create_task, inspect_task
from knowledge_distiller.state import Event, Phase, TransitionFacts
from tests.test_knowledge import SECTIONS, packet


def encoded_packet(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256_id(content):
    return "sha256:" + hashlib.sha256(content).hexdigest()


def source_artifacts(active_until=4_102_444_800):
    result = {}
    for source, snapshot_id, span_id in (
            ("lark", "sha256:" + "a" * 64,
             "hmac-sha256:" + format(1, "064x")),
            ("codex", "sha256:" + "b" * 64,
             "hmac-sha256:" + format(2, "064x"))):
        grant_record = {
            "record_type": "content-grant", "revoked": False,
            "record_id": "grant-" + source,
            "task_id": "task-private", "issuer": "user-987",
            "active_principal": "user-987", "tenant_account": "tenant-private",
            "selector": "DocABC123" if source == "lark" else "session-private",
            "expires_at": active_until,
            "derived_processing_until": active_until,
        }
        grant_record["decision_digest"] = sha256_id(canonical_json(grant_record))
        grant = canonical_json(grant_record)
        authority_record = {
            "record_type": "authority-attestation", "revoked": False,
            "record_id": "authority-" + source,
            "task_id": "task-private", "issuer": "authority-private",
            "active_principal": "user-987", "tenant_account": "tenant-private",
            "selector": "DocABC123" if source == "lark" else "session-private",
            "content_owner": "user-987",
            "expires_at": active_until,
            "derived_processing_until": active_until,
        }
        authority_record["decision_digest"] = sha256_id(
            canonical_json(authority_record))
        authority = canonical_json(authority_record)
        grant_id = grant_record["decision_digest"]
        authority_id = authority_record["decision_digest"]
        native_id = "sha256:" + "c" * 64
        run_id = "sha256:" + "d" * 64
        nodes = (
            ("redacted-span", span_id),
            ("native-locator", native_id),
            ("source-snapshot", snapshot_id),
            ("ingestion-run", run_id),
            ("content-grant", grant_id),
            ("authority-attestation", authority_id),
        )
        relations = (
            "native-locator", "source-snapshot", "ingestion-run",
            "content-grant", "authority-attestation",
        )
        derivations = [{
            "relation": relation,
            "from_node": {"kind": nodes[index][0], "digest": nodes[index][1]},
            "to_node": {"kind": nodes[index + 1][0], "digest": nodes[index + 1][1]},
        } for index, relation in enumerate(relations)]
        excerpt = "Synthetic redacted evidence " + (
            "ev-document" if source == "lark" else "ev-session")
        result.update({
            f"sources/{source}-snapshot.json": canonical_json({
                "manifest": {
                    "source_kind": "document" if source == "lark" else "session",
                    "source_snapshot_id": snapshot_id,
                },
                "payload": {"source_snapshot_id": snapshot_id},
            }),
            f"evidence/{source}-native.json": canonical_json({
                "source_snapshot_id": snapshot_id}),
            f"provenance/{source}-spans.json": canonical_json({
                "schema_version": "knowledge-distiller.provenance/v1",
                "source_snapshot_id": snapshot_id,
                "spans": [{
                    "span_id": span_id, "text": excerpt,
                    "claim_eligible": True, "derivations": derivations,
                }],
            }),
            f"grants/{source}-content-grant.json": grant,
            f"grants/{source}-authority-attestation.json": authority,
        })
    return result


def prepare_review_task(root, stored=None):
    initial = create_task(root)
    with TaskCoordinator(root) as coordinator:
        if stored is None:
            coordinator.transition(
                Event.START_DISTILL,
                TransitionFacts(
                    selected_capability=True, authorized_snapshots=True))
        else:
            with coordinator.artifact_transaction(
                    "seed-sources", initial.generation_id) as transaction:
                for path, content in sorted(stored.items()):
                    transaction.add(path, content)
                transaction.commit(
                    Event.START_DISTILL,
                    TransitionFacts(
                        selected_capability=True, authorized_snapshots=True))
        coordinator.transition(
            Event.CAPABILITY_SELECTED,
            TransitionFacts(selected_capability=True))
        return coordinator.transition(
            Event.EVIDENCE_EXTRACTED,
            TransitionFacts(evidence_complete=True, high_impact_conflict=True))


class CapabilityCompilerTest(unittest.TestCase):
    def test_public_errors_cannot_echo_caller_controlled_values(self):
        error = compiler.CompilerError("PRIVATE-CALLER-VALUE")
        self.assertEqual(error.code, "compilation-failed")
        self.assertNotIn("PRIVATE", str(error))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "task"
        self.review_state = prepare_review_task(self.root, source_artifacts())
        self.assertEqual(self.review_state.state.phase, Phase.CLAIM_REVIEW)

    def compilable_packet(self):
        value = packet()
        value["questions"] = []
        return value

    def adjudicate(self, value, transaction_id="adjudicate-synthetic"):
        return compiler.adjudicate_knowledge_packet(
            self.root, encoded_packet(value), transaction_id,
            self.review_state.generation_id,
        )

    def test_compiles_all_sections_validates_and_persists_exact_draft_bytes(self):
        raw = encoded_packet(self.compilable_packet())
        compile_state = self.adjudicate(self.compilable_packet())
        with mock.patch.object(
                artifacts, "validate_draft", wraps=artifacts.validate_draft) as validate:
            result = compiler.compile_capability(
                self.root, raw, "compile-synthetic",
                compile_state.generation_id,
            )

        self.assertEqual(result.status, "compiled")
        self.assertEqual(result.phase, "evaluate")
        self.assertEqual(validate.call_count, 1)
        self.assertEqual([record.path for record in result.manifest], [
            "SKILL.md", "references/capability.md"])
        self.assertEqual(set(result.draft_files), {
            "SKILL.md", "references/capability.md"})

        generation = self.root / "generations" / result.generation_id
        for relative_path, content in result.draft_files.items():
            persisted = generation / "draft-skill" / relative_path
            self.assertEqual(persisted.read_bytes(), content)
            record = next(item for item in result.manifest if item.path == relative_path)
            self.assertEqual(record.sha256, hashlib.sha256(content).hexdigest())

        manifest = json.loads(
            (generation / "draft-skill/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest, [asdict(record) for record in result.manifest])
        self.assertEqual(
            (generation / "model/adjudicated-knowledge-packet.json").read_bytes(), raw)
        self.assertEqual(inspect_task(self.root).state.phase, Phase.EVALUATE)
        reference = result.draft_files["references/capability.md"].decode("utf-8")
        for section in SECTIONS:
            self.assertIn(compiler.SECTION_HEADINGS[section], reference)

    def test_draft_contains_guidance_but_no_private_provenance_or_evidence(self):
        value = self.compilable_packet()
        selected = value["candidates"][0]
        selected.update(
            name="PRIVATE-PARTICIPANT-NAME",
            purpose="PRIVATE-SELECTOR-TOKEN",
            triggers=["PRIVATE-TRIGGER-TOKEN"],
            outcome="PRIVATE-GRANT-TOKEN",
        )
        raw = encoded_packet(value)
        compile_state = self.adjudicate(value)

        result = compiler.compile_capability(
            self.root, raw, "compile-private-boundary",
            compile_state.generation_id,
        )

        draft = b"\n".join(result.draft_files.values())
        for forbidden in (
                b"Synthetic redacted evidence", b"ev-document", b"cl-triggers",
                b"hmac-sha256:", b"sha256:", b"current-user",
                b"PRIVATE-PARTICIPANT-NAME", b"PRIVATE-SELECTOR-TOKEN",
                b"PRIVATE-TRIGGER-TOKEN", b"PRIVATE-GRANT-TOKEN"):
            self.assertNotIn(forbidden, draft)
        self.assertIn(b"Use the bounded synthetic guidance for triggers.", draft)
        self.assertFalse((self.root / "SKILL.md").exists())

    def test_unresolved_claim_question_or_nonpublishable_rule_blocks_compilation(self):
        cases = []
        unresolved = self.compilable_packet()
        unresolved["decisions"] = unresolved["decisions"][1:]
        cases.append((unresolved, "unresolved-claim"))
        question = self.compilable_packet()
        question["questions"] = packet()["questions"]
        cases.append((question, "critical-question-pending"))
        private_rule = self.compilable_packet()
        private_rule["claims"][0]["sensitivity"] = "private-evidence"
        cases.append((private_rule, "nonpublishable-claim"))

        for index, (value, code) in enumerate(cases):
            with self.subTest(code=code), self.assertRaises(
                    compiler.CompilerError) as caught:
                compiler.adjudicate_knowledge_packet(
                    self.root, encoded_packet(value), "blocked-" + str(index),
                    self.review_state.generation_id,
                )
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(inspect_task(self.root).generation_id,
                         self.review_state.generation_id)

    def test_tainted_or_executable_guidance_fails_before_any_commit(self):
        tainted = self.compilable_packet()
        sentinel = tainted["evidence"][0]["excerpt"]
        tainted["claims"][0]["statement"] = sentinel
        executable = self.compilable_packet()
        executable["claims"][0]["statement"] = "Run curl https://private.invalid."

        for value, code in (
                (tainted, "private-content-in-draft"),
                (executable, "executable-instruction"),
        ):
            with self.subTest(code=code), self.assertRaises(
                    compiler.CompilerError) as caught:
                self.adjudicate(value, "tainted-" + code)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("private.invalid", str(caught.exception))
        self.assertEqual(inspect_task(self.root).generation_id,
                         self.review_state.generation_id)

    def test_claim_text_cannot_reintroduce_private_control_identifiers(self):
        value = self.compilable_packet()
        value["claims"][0]["statement"] = (
            "Apply DOCABC123 only for USER-987."
        )
        with self.assertRaises(compiler.CompilerError) as caught:
            self.adjudicate(value, "private-control-token")

        self.assertEqual(caught.exception.code, "private-content-in-draft")
        self.assertEqual(inspect_task(self.root).generation_id,
                         self.review_state.generation_id)

    def test_legitimate_domain_concepts_and_short_private_values_do_not_false_match(self):
        value = self.compilable_packet()
        value["claims"][0]["statement"] = (
            "Require each participant to request an access grant and preserve data provenance."
        )
        compile_state = self.adjudicate(value, "legitimate-concepts")

        result = compiler.compile_capability(
            self.root, encoded_packet(value), "compile-legitimate-concepts",
            compile_state.generation_id)

        self.assertEqual(result.phase, "evaluate")
        packet_value = compiler.validate_compilation_input(
            encoded_packet(self.compilable_packet()))
        compiler._reject_private_content(
            packet_value, compiler._render_bundle(packet_value), ("a", "I"))

    def test_short_private_identifiers_do_not_match_unrelated_prose(self):
        value = self.compilable_packet()
        value["evidence"][0]["evidence_id"] = "a"
        for claim in value["claims"]:
            claim["support_evidence_ids"] = ["a"]
        value["candidates"][0]["evidence_ids"] = ["a", "ev-session"]
        value["candidates"][1]["evidence_ids"] = ["a"]
        value["uncertainties"][0]["evidence_ids"] = ["a"]

        compile_state = self.adjudicate(value)
        result = compiler.compile_capability(
            self.root, encoded_packet(value), "short-id",
            compile_state.generation_id,
        )

        self.assertEqual(result.phase, "evaluate")

    def test_closed_packet_and_bundle_reject_scripts_manifests_and_extra_fields(self):
        value = self.compilable_packet()
        value["scripts"] = [{"path": "scripts/tool.py", "content": "PRIVATE"}]
        with self.assertRaises(compiler.CompilerError) as caught:
            compiler.compile_capability(
                self.root, encoded_packet(value), "extra-script",
                self.review_state.generation_id,
            )
        self.assertEqual(caught.exception.code, "unknown-field")
        self.assertNotIn("PRIVATE", str(caught.exception))

        validated = compiler.validate_compilation_input(
            encoded_packet(self.compilable_packet()))
        bundle = compiler._render_bundle(validated)
        self.assertEqual(set(bundle), {"SKILL.md", "references/capability.md"})
        self.assertFalse(any(
            path.casefold() in artifacts.PACKAGE_MANIFESTS or path.startswith("scripts/")
            for path in bundle
        ))

    def test_failed_artifact_validation_never_persists_or_advances_state(self):
        value = self.compilable_packet()
        compile_state = self.adjudicate(value)
        with mock.patch.object(
                artifacts, "validate_draft",
                side_effect=artifacts.DraftValidationError("executable-instruction")):
            with self.assertRaises(compiler.CompilerError) as caught:
                compiler.compile_capability(
                    self.root, encoded_packet(value),
                    "validator-failure", compile_state.generation_id,
                )
        self.assertEqual(caught.exception.code, "executable-instruction")
        self.assertEqual(inspect_task(self.root).generation_id,
                         compile_state.generation_id)

    def test_recompile_replaces_the_complete_prior_draft_bundle(self):
        value = self.compilable_packet()
        stored = source_artifacts()
        stored["draft-skill/references/stale.md"] = b"stale reviewed guidance"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            review = prepare_review_task(root, stored)
            compile_state = compiler.adjudicate_knowledge_packet(
                root, encoded_packet(value), "adjudicate-recompile",
                review.generation_id)

            result = compiler.compile_capability(
                root, encoded_packet(value), "compile-replacement",
                compile_state.generation_id)

            generation = root / "generations" / result.generation_id
            self.assertFalse(
                (generation / "draft-skill/references/stale.md").exists())
            self.assertEqual(
                {path.relative_to(generation / "draft-skill").as_posix()
                 for path in (generation / "draft-skill").rglob("*")
                 if path.is_file()},
                {"SKILL.md", "manifest.json", "references/capability.md"},
            )

    def test_compile_binds_exact_prior_adjudication_and_persisted_provenance(self):
        value = self.compilable_packet()
        changed = deepcopy(value)
        changed["decisions"][0]["rationale"] = "A forged later decision."
        compile_state = self.adjudicate(value)

        with self.assertRaises(compiler.CompilerError) as caught:
            compiler.compile_capability(
                self.root, encoded_packet(changed), "forged-decision",
                compile_state.generation_id,
            )
        self.assertEqual(caught.exception.code, "adjudication-mismatch")

        mismatched = self.compilable_packet()
        mismatched["evidence"][0]["source_snapshot_id"] = "sha256:" + "e" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            review = prepare_review_task(root, source_artifacts())
            with self.assertRaises(compiler.CompilerError) as caught:
                compiler.adjudicate_knowledge_packet(
                    root, encoded_packet(mismatched), "mismatch",
                    review.generation_id)
            self.assertEqual(caught.exception.code, "source-provenance-mismatch")

        stored = source_artifacts()
        stored["sources/lark-snapshot.json"] = canonical_json({
            "manifest": {
                "source_kind": "document",
                "source_snapshot_id": "sha256:" + "f" * 64,
            },
            "payload": {"source_snapshot_id": "sha256:" + "f" * 64},
        })
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            review = prepare_review_task(root, stored)
            with self.assertRaises(compiler.CompilerError) as caught:
                compiler.adjudicate_knowledge_packet(
                    root, encoded_packet(value), "source-mismatch",
                    review.generation_id)
            self.assertEqual(caught.exception.code, "source-provenance-mismatch")

    def test_adjudication_requires_bound_sources_and_active_grants(self):
        value = self.compilable_packet()
        cases = (
            (None, "source-provenance-missing"),
            (source_artifacts(active_until=1), "source-grant-expired"),
        )
        for index, (stored, code) in enumerate(cases):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "task"
                review = prepare_review_task(root, stored)
                with self.assertRaises(compiler.CompilerError) as caught:
                    compiler.adjudicate_knowledge_packet(
                        root, encoded_packet(value), "bound-" + str(index),
                        review.generation_id)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(inspect_task(root).generation_id,
                                 review.generation_id)


if __name__ == "__main__":
    unittest.main()
