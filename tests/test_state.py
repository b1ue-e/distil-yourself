import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller.state import (  # noqa: E402
    Event,
    InvalidTransition,
    Mode,
    Phase,
    TaskState,
    TransitionFacts,
    transition,
)


class StateTransitionTest(unittest.TestCase):
    def test_discover_requires_seed(self) -> None:
        state = TaskState()
        with self.assertRaisesRegex(InvalidTransition, "has_seed"):
            transition(state, Event.START_DISCOVER, TransitionFacts())

        result = transition(
            state,
            Event.START_DISCOVER,
            TransitionFacts(has_seed=True),
        )
        self.assertEqual(result.mode, Mode.DISCOVER)
        self.assertEqual(result.phase, Phase.SCOUT)
        self.assertEqual(state.phase, Phase.INIT)

    def test_distill_requires_selection_and_routes_existing_snapshots(self) -> None:
        with self.assertRaisesRegex(InvalidTransition, "selected_capability"):
            transition(TaskState(), Event.START_DISTILL, TransitionFacts())

        review = transition(
            TaskState(),
            Event.START_DISTILL,
            TransitionFacts(selected_capability=True, authorized_snapshots=True),
        )
        self.assertEqual((review.mode, review.phase), (Mode.DISTILL, Phase.CAPABILITY_REVIEW))

    def test_scout_requires_both_metadata_grants(self) -> None:
        state = TaskState(mode=Mode.DISCOVER, phase=Phase.SCOUT)
        with self.assertRaisesRegex(InvalidTransition, "metadata_grant"):
            transition(
                state,
                Event.SCOUT_COMPLETED,
                TransitionFacts(discovery_grant=True),
            )

        result = transition(
            state,
            Event.SCOUT_COMPLETED,
            TransitionFacts(discovery_grant=True, metadata_grant=True),
        )
        self.assertEqual(result.phase, Phase.SOURCE_REVIEW)

    def test_update_and_repair_have_distinct_guards(self) -> None:
        with self.assertRaisesRegex(InvalidTransition, "parent_approved"):
            transition(TaskState(), Event.START_UPDATE, TransitionFacts())

        update = transition(
            TaskState(),
            Event.START_UPDATE,
            TransitionFacts(
                parent_approved=True,
                subject_valid=True,
                state_resolved=True,
            ),
        )
        self.assertEqual((update.mode, update.phase), (Mode.UPDATE, Phase.SOURCE_REVIEW))

        repair = transition(
            TaskState(),
            Event.START_REPAIR,
            TransitionFacts(
                parent_approved=True,
                subject_valid=True,
                revocation_verified=True,
            ),
        )
        self.assertEqual(
            (repair.mode, repair.phase, repair.prior_phase, repair.epoch),
            (Mode.UPDATE, Phase.AUTH_STALE, Phase.SOURCE_REVIEW, 1),
        )

    def test_state_rejects_impossible_topology(self) -> None:
        with self.assertRaisesRegex(ValueError, "epoch"):
            TaskState(epoch=True)
        with self.assertRaisesRegex(ValueError, "mode"):
            TaskState(phase=Phase.COMPILE)
        with self.assertRaisesRegex(ValueError, "prior_phase"):
            TaskState(
                mode=Mode.DISTILL,
                phase=Phase.SUSPENDED,
                prior_phase=Phase.DONE,
            )
        with self.assertRaisesRegex(ValueError, "prior_phase"):
            TaskState(mode=Mode.DISTILL, phase=Phase.AUTH_STALE)

    def test_transition_facts_require_runtime_boolean_types(self) -> None:
        with self.assertRaisesRegex(ValueError, "content_grant"):
            TransitionFacts(content_grant="false")
        with self.assertRaisesRegex(ValueError, "revision_target"):
            TransitionFacts(revision_target="evaluate")

    def test_content_ingestion_requires_content_and_authority(self) -> None:
        state = TaskState(mode=Mode.DISTILL, phase=Phase.SOURCE_REVIEW)
        with self.assertRaisesRegex(InvalidTransition, "authority_valid"):
            transition(
                state,
                Event.CONTENT_GRANTED,
                TransitionFacts(content_grant=True),
            )
        result = transition(
            state,
            Event.CONTENT_GRANTED,
            TransitionFacts(content_grant=True, authority_valid=True),
        )
        self.assertEqual(result.phase, Phase.INGEST)

        partial = transition(
            result,
            Event.SOURCE_SNAPSHOTTED,
            TransitionFacts(source_ready=True),
        )
        self.assertEqual(partial.phase, Phase.INGEST)
        completed = transition(
            partial,
            Event.SOURCES_SNAPSHOTTED,
            TransitionFacts(sources_ready=True),
        )
        self.assertEqual(completed.phase, Phase.CAPABILITY_REVIEW)

    def test_rejecting_all_sources_ends_with_partial_report(self) -> None:
        state = TaskState(mode=Mode.DISCOVER, phase=Phase.SOURCE_REVIEW)
        result = transition(state, Event.SOURCES_REJECTED, TransitionFacts())
        self.assertEqual(result.phase, Phase.DONE_PARTIAL)

    def test_extraction_always_routes_through_claim_review(self) -> None:
        state = TaskState(mode=Mode.DISTILL, phase=Phase.EXTRACT)
        review = transition(
            state,
            Event.EVIDENCE_EXTRACTED,
            TransitionFacts(evidence_complete=True),
        )
        self.assertEqual(review.phase, Phase.CLAIM_REVIEW)

        apparently_resolved = transition(
            state,
            Event.EVIDENCE_EXTRACTED,
            TransitionFacts(evidence_complete=True, claims_resolved=True),
        )
        self.assertEqual(apparently_resolved.phase, Phase.CLAIM_REVIEW)

    def test_revision_can_return_to_evaluation_and_increments_epoch(self) -> None:
        state = TaskState(mode=Mode.UPDATE, phase=Phase.REVISION_REVIEW, epoch=4)
        result = transition(
            state,
            Event.REVISION_ACCEPTED,
            TransitionFacts(revision_target=Phase.EVALUATE),
        )
        self.assertEqual((result.phase, result.epoch), (Phase.EVALUATE, 5))

    def test_authorization_repair_requires_purge_and_increments_epoch(self) -> None:
        active = TaskState(mode=Mode.DISTILL, phase=Phase.EXTRACT, epoch=2)
        stale = transition(active, Event.AUTH_INVALID, TransitionFacts())
        self.assertEqual((stale.phase, stale.prior_phase), (Phase.AUTH_STALE, Phase.EXTRACT))

        with self.assertRaisesRegex(InvalidTransition, "cannot pause"):
            transition(stale, Event.PAUSE, TransitionFacts())

        with self.assertRaisesRegex(InvalidTransition, "purge_complete"):
            transition(
                stale,
                Event.AUTH_RESTORED,
                TransitionFacts(auth_restored=True, revision_target=Phase.SOURCE_REVIEW),
            )
        restored = transition(
            stale,
            Event.AUTH_RESTORED,
            TransitionFacts(
                auth_restored=True,
                purge_complete=True,
                revision_target=Phase.SOURCE_REVIEW,
            ),
        )
        self.assertEqual(
            (restored.phase, restored.prior_phase, restored.epoch),
            (Phase.SOURCE_REVIEW, None, 3),
        )

    def test_failed_evaluation_without_budget_suspends_at_evaluation(self) -> None:
        state = TaskState(mode=Mode.DISTILL, phase=Phase.EVALUATE)
        exhausted = transition(state, Event.EVALUATION_FAILED, TransitionFacts())
        self.assertEqual(
            (exhausted.phase, exhausted.prior_phase),
            (Phase.SUSPENDED_EXHAUSTED, Phase.EVALUATE),
        )

    def test_pause_and_resume_restore_the_recorded_phase(self) -> None:
        state = TaskState(mode=Mode.DISTILL, phase=Phase.EXTRACT, epoch=2)
        paused = transition(state, Event.PAUSE, TransitionFacts())
        self.assertEqual((paused.phase, paused.prior_phase), (Phase.SUSPENDED, Phase.EXTRACT))

        with self.assertRaisesRegex(InvalidTransition, "resume_valid"):
            transition(paused, Event.RESUME, TransitionFacts())
        resumed = transition(paused, Event.RESUME, TransitionFacts(resume_valid=True))
        self.assertEqual((resumed.phase, resumed.prior_phase), (Phase.EXTRACT, None))

    def test_budget_extension_is_typed_and_resumable(self) -> None:
        active = TaskState(mode=Mode.DISCOVER, phase=Phase.SCOUT)
        exhausted = transition(active, Event.BUDGET_EXHAUSTED, TransitionFacts())
        self.assertEqual(exhausted.phase, Phase.SUSPENDED_EXHAUSTED)

        with self.assertRaisesRegex(InvalidTransition, "budget_extended"):
            transition(exhausted, Event.BUDGET_EXTENDED, TransitionFacts(resume_valid=True))
        resumed = transition(
            exhausted,
            Event.BUDGET_EXTENDED,
            TransitionFacts(resume_valid=True, budget_extended=True),
        )
        self.assertEqual(resumed.phase, Phase.SCOUT)

    def test_terminal_state_rejects_further_transitions(self) -> None:
        for phase in (
            Phase.DONE,
            Phase.DONE_APPROVED,
            Phase.DONE_PARTIAL,
            Phase.CANCELLED,
            Phase.FAILED_PERMANENT,
        ):
            with self.subTest(phase=phase):
                with self.assertRaisesRegex(InvalidTransition, "terminal"):
                    transition(
                        TaskState(mode=Mode.DISTILL, phase=phase),
                        Event.CANCEL,
                        TransitionFacts(),
                    )


if __name__ == "__main__":
    unittest.main()
