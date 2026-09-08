# Knowledge Packet Contract

Use schema `knowledge-distiller.knowledge-packet/v1`. The validator in
`scripts/knowledge_distiller/knowledge.py` is the machine authority. All objects
are closed: every listed field is required and unknown fields are rejected. The
packet is private; never print evidence excerpts, selectors, or provenance.

## Top-level records

| Field | Required shape |
| --- | --- |
| `schema_version` | Exact schema string above |
| `evidence` | One or more EvidenceRecord objects |
| `claims` | One or more proposed Claim objects |
| `decisions` | Zero or more ClaimDecision objects |
| `candidates` | One or more CapabilityCandidate objects |
| `model` | One CapabilityModel object |
| `questions` | Zero or more CriticalQuestion objects |
| `uncertainties` | Zero or more lower-impact Uncertainty objects |

Common identifiers are unique, non-empty strings of at most 256 UTF-8 bytes.
Confidence is an integer from 0 through 3. Snapshot IDs are
`sha256:` plus 64 lowercase hex digits; span IDs are `hmac-sha256:` plus 64.
The complete JSON is at most 4 MiB and must pass the duplicate-key-safe decoder.

## Record fields

- EvidenceRecord: `evidence_id`, `source_kind` (`document` or `session`),
  `source_snapshot_id`, non-empty `redacted_span_ids`, `evidence_type`
  (`observation`, `owner-statement`, or `inference`), `excerpt` (8..16384 UTF-8
  bytes), non-empty `applicability`, `confidence`, `freshness` (`current`,
  `recent`, `historical`, or `unknown`), `freshness_reason`, and `sensitivity`
  (`private-evidence`, `restricted`, or `publishable-guidance`).
- Claim: `claim_id`, `claim_type` using the evidence-type enum, `statement`,
  non-empty `redacted_span_ids`, non-empty `support_evidence_ids`,
  `contradiction_evidence_ids`, `confidence`, `freshness`, `freshness_reason`,
  `sensitivity`, `impact` (`low`, `medium`, or `high`), `rule_kind` (`guidance`
  or `hard-invariant`), and exact `status: "proposed"`. Support and contradiction
  must not overlap; every claim span must belong to supporting evidence.
- ClaimDecision: `decision_id`, `claim_id`, `outcome` (`confirmed`, `rejected`,
  or `superseded`), exact `decided_by: "current-user"`, `rationale`, and
  `superseded_by`. Only `superseded` uses a non-null decision ID. This marker is
  structural, not authentication; create it only after explicit user confirmation.
- CapabilityCandidate: `capability_id`, `name`, `purpose`, non-empty `triggers`,
  `outcome`, non-empty `evaluation_scenarios`, `recurrence_count` (1..1000000),
  `decision_impact`, and non-empty `evidence_ids`.
- CapabilityModel: `model_id`, `selected_capability_id`, `candidate_ids` matching
  the candidate set exactly, and `sections`. Every section is a non-empty array
  of existing claim IDs: `triggers`, `non_triggers`, `goals`, `non_goals`,
  `inputs`, `outputs`, `invariants`, `cues`, `decision_rules`, `workflow`,
  `exceptions`, `failures`, `examples`, and `dependencies`.
- CriticalQuestion: `question_id`, `reason` (`new-authority-required`,
  `hard-constraint-unsupported`, `critical-branch-blocked`, or
  `competing-rules`), `prompt`, two to four `alternatives`, non-empty
  `evidence_ids`, exact `behavioral_impact: "high"`, `confidence`, and nullable
  `recommendation`. Each alternative has `alternative_id`, `label`,
  `evidence_ids`, and `behavioral_impact`. A recommendation must name an
  alternative backed by a non-empty subset of the question evidence.
- Uncertainty: `uncertainty_id`, `summary`, non-empty `evidence_ids`, and
  `behavioral_impact` (`low` or `medium`).

## Schema-valid synthetic skeleton

This skeleton validates structurally. Replace IDs and private evidence only with
values from the authorized task artifacts; never fabricate provenance.

```json
{
  "schema_version": "knowledge-distiller.knowledge-packet/v1",
  "evidence": [{
    "evidence_id": "ev-1",
    "source_kind": "document",
    "source_snapshot_id": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "redacted_span_ids": ["hmac-sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
    "evidence_type": "owner-statement",
    "excerpt": "Synthetic redacted evidence.",
    "applicability": ["bounded task"],
    "confidence": 3,
    "freshness": "current",
    "freshness_reason": "Current authorized snapshot.",
    "sensitivity": "private-evidence"
  }],
  "claims": [{
    "claim_id": "cl-rule",
    "claim_type": "owner-statement",
    "statement": "Apply the confirmed bounded rule.",
    "redacted_span_ids": ["hmac-sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
    "support_evidence_ids": ["ev-1"],
    "contradiction_evidence_ids": [],
    "confidence": 3,
    "freshness": "current",
    "freshness_reason": "Current authorized snapshot.",
    "sensitivity": "publishable-guidance",
    "impact": "high",
    "rule_kind": "hard-invariant",
    "status": "proposed"
  }],
  "decisions": [{
    "decision_id": "dec-rule",
    "claim_id": "cl-rule",
    "outcome": "confirmed",
    "decided_by": "current-user",
    "rationale": "Explicit current-user confirmation.",
    "superseded_by": null
  }],
  "candidates": [{
    "capability_id": "cap-1",
    "name": "Bounded capability",
    "purpose": "Apply confirmed guidance.",
    "triggers": ["A bounded case needs this rule."],
    "outcome": "A reviewable result.",
    "evaluation_scenarios": ["A synthetic boundary case."],
    "recurrence_count": 1,
    "decision_impact": "high",
    "evidence_ids": ["ev-1"]
  }],
  "model": {
    "model_id": "model-1",
    "selected_capability_id": "cap-1",
    "candidate_ids": ["cap-1"],
    "sections": {
      "triggers": ["cl-rule"],
      "non_triggers": ["cl-rule"],
      "goals": ["cl-rule"],
      "non_goals": ["cl-rule"],
      "inputs": ["cl-rule"],
      "outputs": ["cl-rule"],
      "invariants": ["cl-rule"],
      "cues": ["cl-rule"],
      "decision_rules": ["cl-rule"],
      "workflow": ["cl-rule"],
      "exceptions": ["cl-rule"],
      "failures": ["cl-rule"],
      "examples": ["cl-rule"],
      "dependencies": ["cl-rule"]
    }
  },
  "questions": [],
  "uncertainties": []
}
```

## Question resolution and adjudication

Run `validate-knowledge-packet`, then `next-critical-question`. Ask only the one
returned question. A behavior or claim question answer must become ClaimDecision
data for each affected publishable claim; update proposed claims when the answer
changes their wording or behavior. A `new-authority-required` question can be
resolved only when the trusted broker obtains a new active ContentGrant and
AuthorityAttestation, or when the unauthorized source and dependent claims are
excluded. Never encode missing authority as ClaimDecision.

Remove a question only after its applicable resolution is complete. Questions
must be empty before adjudication. Every claim referenced by `model.sections`
must have a `confirmed` ClaimDecision and `publishable-guidance` sensitivity.

Obtain explicit current-user confirmation for the selected capability and every
published claim before encoding decisions. Then adjudicate and compile using the
exact same packet bytes; reserialization, reordered keys, or changed whitespace
creates an adjudication mismatch. CLI summaries never return private excerpts.
