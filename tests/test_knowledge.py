"""Strict synthetic tests for the evidence-to-capability knowledge contract."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller import knowledge


SECTIONS = (
    "triggers", "non_triggers", "goals", "non_goals", "inputs", "outputs",
    "invariants", "cues", "decision_rules", "workflow", "exceptions",
    "failures", "examples", "dependencies",
)


def span(index):
    return "hmac-sha256:" + format(index, "064x")


def evidence(identifier, source_kind, span_id, evidence_type="observation"):
    return {
        "evidence_id": identifier,
        "source_kind": source_kind,
        "source_snapshot_id": "sha256:" + ("a" if source_kind == "document" else "b") * 64,
        "redacted_span_ids": [span_id],
        "evidence_type": evidence_type,
        "excerpt": "Synthetic redacted evidence " + identifier,
        "applicability": ["bounded incident response"],
        "confidence": 3,
        "freshness": "current",
        "freshness_reason": "Observed in the current synthetic snapshot.",
        "sensitivity": "private-evidence",
    }


def claim(section, evidence_id="ev-document", span_id=None):
    span_id = span_id or span(1)
    return {
        "claim_id": "cl-" + section,
        "claim_type": "owner-statement" if section == "invariants" else "observation",
        "statement": "Use the bounded synthetic guidance for " + section.replace("_", " ") + ".",
        "redacted_span_ids": [span_id],
        "support_evidence_ids": [evidence_id],
        "contradiction_evidence_ids": [],
        "confidence": 3,
        "freshness": "current",
        "freshness_reason": "The supporting snapshot is current.",
        "sensitivity": "publishable-guidance",
        "impact": "high" if section == "invariants" else "medium",
        "rule_kind": "hard-invariant" if section == "invariants" else "guidance",
        "status": "proposed",
    }


def candidate(identifier, evidence_ids, recurrence, impact, scenarios):
    return {
        "capability_id": identifier,
        "name": "Bounded Review " + identifier,
        "purpose": "Review synthetic evidence without expanding authority.",
        "triggers": ["Review a bounded synthetic incident"],
        "outcome": "A reviewable evidence-grounded decision.",
        "evaluation_scenarios": ["Synthetic case " + str(index) for index in range(scenarios)],
        "recurrence_count": recurrence,
        "decision_impact": impact,
        "evidence_ids": evidence_ids,
    }


def packet():
    claims = [claim(section) for section in SECTIONS]
    decisions = [{
        "decision_id": "dec-" + section,
        "claim_id": "cl-" + section,
        "outcome": "confirmed",
        "decided_by": "current-user",
        "rationale": "Explicit synthetic confirmation.",
        "superseded_by": None,
    } for section in SECTIONS]
    return {
        "schema_version": "knowledge-distiller.knowledge-packet/v1",
        "evidence": [
            evidence("ev-document", "document", span(1), "owner-statement"),
            evidence("ev-session", "session", span(2)),
        ],
        "claims": claims,
        "decisions": decisions,
        "candidates": [
            candidate("cap-selected", ["ev-document", "ev-session"], 5, "high", 2),
            candidate("cap-other", ["ev-document"], 5, "high", 2),
        ],
        "model": {
            "model_id": "model-1",
            "selected_capability_id": "cap-selected",
            "candidate_ids": ["cap-selected", "cap-other"],
            "sections": {section: ["cl-" + section] for section in SECTIONS},
        },
        "questions": [{
            "question_id": "q-rules",
            "reason": "competing-rules",
            "prompt": "Which bounded rule should govern the synthetic conflict?",
            "alternatives": [
                {"alternative_id": "a-safe", "label": "Prefer the bounded rule",
                 "evidence_ids": ["ev-document"], "behavioral_impact": "high"},
                {"alternative_id": "a-fast", "label": "Prefer the faster rule",
                 "evidence_ids": ["ev-session"], "behavioral_impact": "high"},
            ],
            "evidence_ids": ["ev-document", "ev-session"],
            "behavioral_impact": "high",
            "confidence": 2,
            "recommendation": None,
        }],
        "uncertainties": [{
            "uncertainty_id": "u-wording",
            "summary": "The exact heading wording is uncertain.",
            "evidence_ids": ["ev-document"],
            "behavioral_impact": "low",
        }],
    }


class KnowledgeContractTest(unittest.TestCase):
    def test_public_errors_cannot_echo_caller_controlled_values(self):
        error = knowledge.KnowledgeError("PRIVATE-CALLER-VALUE")
        self.assertEqual(error.code, "knowledge-invalid")
        self.assertNotIn("PRIVATE", str(error))

    def test_valid_packet_links_claims_scores_candidates_and_selects_one_question(self):
        result = knowledge.validate_packet(packet())

        self.assertEqual(result.model.selected_capability_id, "cap-selected")
        self.assertEqual(
            [(score.capability_id, score.recurrence, score.decision_impact,
              score.evidence_coverage, score.testability, score.total)
             for score in result.scores],
            [("cap-selected", 3, 3, 2, 2, 10),
             ("cap-other", 3, 3, 1, 2, 9)])
        self.assertEqual(result.scores[0].evidence_ids,
                         ("ev-document", "ev-session"))
        self.assertIn("decision impact", result.scores[0].tie_break_rationale)
        question = knowledge.next_critical_question(result)
        self.assertEqual(question.question_id, "q-rules")
        self.assertIsNone(question.recommendation)
        with self.assertRaises(TypeError):
            result.model.sections["triggers"] = ("cl-other",)

    def test_claims_remain_proposed_until_an_explicit_current_user_decision(self):
        value = packet()
        value["decisions"] = []
        unresolved = knowledge.validate_packet(value)
        self.assertEqual(knowledge.claim_outcome(unresolved, "cl-invariants"), "proposed")

        value = packet()
        value["claims"][0]["status"] = "confirmed"
        with self.assertRaises(knowledge.KnowledgeError) as caught:
            knowledge.validate_packet(value)
        self.assertEqual(str(caught.exception), caught.exception.code)

        value = packet()
        value["decisions"][0]["decided_by"] = "model"
        with self.assertRaises(knowledge.KnowledgeError) as caught:
            knowledge.validate_packet(value)
        self.assertEqual(caught.exception.code, "invalid-claim-decision")

    def test_claim_support_and_span_provenance_are_closed_and_complete(self):
        for mutation, code in (
                (lambda value: value["claims"][0].update(support_evidence_ids=["ev-missing"]),
                 "unknown-evidence"),
                (lambda value: value["claims"][0].update(redacted_span_ids=[span(99)]),
                 "claim-provenance-mismatch"),
                (lambda value: value["claims"][0].update(
                    contradiction_evidence_ids=["ev-document"]),
                 "claim-evidence-overlap"),
                (lambda value: value["evidence"][0].update(private_extra="PRIVATE"),
                 "unknown-field")):
            value = packet()
            mutation(value)
            with self.subTest(code=code), self.assertRaises(knowledge.KnowledgeError) as caught:
                knowledge.validate_packet(value)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("PRIVATE", str(caught.exception))

        value = packet()
        value["claims"][0].update(
            redacted_span_ids=[span(2)],
            contradiction_evidence_ids=["ev-session"],
        )
        with self.assertRaises(knowledge.KnowledgeError) as caught:
            knowledge.validate_packet(value)
        self.assertEqual(caught.exception.code, "claim-provenance-mismatch")

    def test_capability_ranking_uses_impact_then_testability_then_stable_id(self):
        value = packet()
        value["candidates"] = [
            candidate("cap-low-impact", ["ev-document", "ev-session"], 5, "medium", 3),
            candidate("cap-high-impact", ["ev-document", "ev-session"], 5, "high", 2),
            candidate("cap-more-testable", ["ev-document", "ev-session"], 5, "high", 3),
        ]
        value["model"]["candidate_ids"] = [item["capability_id"] for item in value["candidates"]]
        value["model"]["selected_capability_id"] = "cap-more-testable"

        result = knowledge.validate_packet(value)

        self.assertEqual([item.capability_id for item in result.scores], [
            "cap-more-testable", "cap-high-impact", "cap-low-impact"])
        self.assertEqual([item.total for item in result.scores], [11, 10, 10])

    def test_question_priority_and_recommendations_require_support(self):
        value = packet()
        authority = deepcopy(value["questions"][0])
        authority.update(
            question_id="q-authority", reason="new-authority-required",
            recommendation="a-safe")
        value["questions"].append(authority)

        result = knowledge.validate_packet(value)
        self.assertEqual(
            knowledge.next_critical_question(result).question_id, "q-authority")
        self.assertEqual(len(result.uncertainties), 1)

        value["questions"][1]["alternatives"][0]["evidence_ids"] = []
        with self.assertRaises(knowledge.KnowledgeError) as caught:
            knowledge.validate_packet(value)
        self.assertEqual(caught.exception.code, "unsupported-recommendation")

    def test_duplicate_json_keys_and_private_diagnostics_fail_closed(self):
        raw = json.dumps(packet(), separators=(",", ":")).encode("utf-8")
        self.assertEqual(knowledge.decode_packet(raw)["schema_version"],
                         "knowledge-distiller.knowledge-packet/v1")
        with self.assertRaises(knowledge.KnowledgeError) as caught:
            knowledge.decode_packet(b'{"schema_version":"PRIVATE","schema_version":"other"}')
        self.assertEqual(caught.exception.code, "duplicate-json-key")
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_snapshot_and_span_digest_domains_cannot_be_exchanged(self):
        value = packet()
        value["evidence"][0]["source_snapshot_id"] = span(8)
        with self.assertRaises(knowledge.KnowledgeError) as caught:
            knowledge.validate_packet(value)
        self.assertEqual(caught.exception.code, "invalid-snapshot-id")

        value = packet()
        value["evidence"][0]["redacted_span_ids"] = [
            "sha256:" + "8" * 64]
        with self.assertRaises(knowledge.KnowledgeError) as caught:
            knowledge.validate_packet(value)
        self.assertEqual(caught.exception.code, "invalid-span-id")

        value = packet()
        value["evidence"][0]["excerpt"] = "SECRET"
        with self.assertRaises(knowledge.KnowledgeError) as caught:
            knowledge.validate_packet(value)
        self.assertEqual(caught.exception.code, "invalid-text")

    def test_recommendation_evidence_must_belong_to_the_question(self):
        value = packet()
        value["questions"][0]["recommendation"] = "a-safe"
        value["questions"][0]["evidence_ids"] = ["ev-session"]
        with self.assertRaises(knowledge.KnowledgeError) as caught:
            knowledge.validate_packet(value)
        self.assertEqual(caught.exception.code, "unsupported-recommendation")


if __name__ == "__main__":
    unittest.main()
