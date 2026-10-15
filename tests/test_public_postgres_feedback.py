"""Real PostgreSQL parity for human feedback, forks and shared-record lifecycle."""

import pytest
import test_public_bot_developer as bot_developer
import test_public_bot_diagnostics as bot_diagnostics
import test_public_bot_playback as bot_playback
import test_public_bot_runtime as bot_runtime
import test_public_chat_feedback as feedback
import test_public_knowledge as knowledge
import test_public_ledger_convergence as convergence
import test_public_ledger_human_checks as checks
import test_public_message_edits as edits
import test_public_usage as usage
from postgres_fixture import postgres_configuration as postgres_configuration
from test_public_server import installation as sqlite_installation


@pytest.fixture
def installation(postgres_configuration, tmp_path):
    return sqlite_installation.__wrapped__(tmp_path)


test_human_reactions = feedback.test_reactions_toggle_idempotently_and_use_recorded_model
test_reactor_identity_bounds = feedback.test_reactor_identities_are_bounded_and_do_not_expose_contact_information
test_single_reaction_aliases = feedback.test_one_reaction_replaces_prior_choice_across_canonical_output_aliases
test_concurrent_reaction_changes = feedback.test_concurrent_reaction_changes_leave_exactly_one_choice
test_reaction_migration = feedback.test_reaction_migration_keeps_latest_choice_and_deterministic_ties
test_private_feedback = feedback.test_private_model_feedback_is_filtered_for_each_reader
test_beta_fork = feedback.test_fork_retains_cross_model_history_settings_and_excludes_future_state
test_fork_scope = feedback.test_fork_authorization_kind_and_message_cutoff_are_enforced
test_human_check_revision = checks.test_human_check_pins_content_actor_and_time_without_promoting_claim
test_human_check_refinement = checks.test_new_revision_does_not_inherit_a_human_check_and_stale_new_checks_are_refused
test_ledger_convergence = convergence.test_ordinary_beta_delivers_exact_reusable_result_after_independent_review
test_pinned_reviewer_catalogue = convergence.test_pinned_reviewer_is_not_revoked_by_catalogue_suggestion_changes
test_proof_target_repair = convergence.test_proof_target_concurrent_revision_is_refused_then_reconciled_without_duplicate
test_proof_revision_repair = convergence.test_exact_proof_revision_reads_and_stale_feedback_enable_informed_repair
test_current_model_catalogue = usage.test_key_setup_and_new_catalogue_models_need_no_model_policy
test_message_edit_history = edits.test_message_edits_history_retry_updates_and_mentions
test_message_edit_authority = edits.test_message_edit_requires_current_topic_write_access_and_human_author
test_message_edit_concurrent = edits.test_message_edit_concurrent_drafts_have_one_winner
test_message_edit_captured_context = edits.test_edit_preserves_accepted_context_and_historical_compaction


def test_developer_diagnostics(postgres_configuration, tmp_path):
    bot_diagnostics.test_developer_diagnostics_are_topic_scoped_and_do_not_read_or_forward_content(tmp_path)


@pytest.mark.parametrize("case", ["authorization", "receipt", "private_preview"])
def test_developer_issue_policy(postgres_configuration, tmp_path, case):
    fixture = bot_developer.fixture.__wrapped__(tmp_path)
    test = {"authorization": bot_developer.test_developer_disabled_default_and_exact_owner_repo_requester_policy,
            "receipt": bot_developer.test_exact_issue_approval_consume_once_and_result_receipt,
            "private_preview": bot_developer.test_private_issue_preview_disappears_after_owner_loses_access}[case]
    test(fixture)


def test_bot_edits_before_capture(postgres_configuration, tmp_path):
    bot_runtime.test_edits_before_bot_capture_preserve_original_event_and_request(bot_runtime.fixture.__wrapped__(tmp_path))


@pytest.mark.parametrize("state", ["draft", "published"])
def test_blog_deletion(postgres_configuration, tmp_path, state):
    env = knowledge.installation.__wrapped__(tmp_path)
    knowledge.test_blog_deletion_hides_all_surfaces_and_retains_history(env, state)


def test_blog_deletion_permissions(postgres_configuration, tmp_path):
    env = knowledge.installation.__wrapped__(tmp_path)
    knowledge.test_blog_deletion_requires_current_revision_permissions_and_exact_request(env)


def test_bot_stop_queue(postgres_configuration, tmp_path):
    bot_playback.test_queued_stop_is_idempotent_and_never_claimed(bot_playback.fixture.__wrapped__(tmp_path))


@pytest.mark.parametrize("approval_state", [None, "pending", "approved", "consumed"])
def test_bot_stop_lease(postgres_configuration, tmp_path, approval_state):
    bot_playback.test_claimed_stop_fences_lease_and_preserves_unknown_side_effects(
        bot_playback.fixture.__wrapped__(tmp_path), approval_state
    )


def test_bot_stop_authority(postgres_configuration, tmp_path):
    bot_playback.test_stop_requires_current_scope_and_requester_owner_or_administrator(bot_playback.fixture.__wrapped__(tmp_path))


@pytest.mark.parametrize("operation", ["claim", "finish", "approval"])
def test_bot_stop_concurrent(postgres_configuration, tmp_path, operation):
    test = {
        "claim": bot_playback.test_concurrent_claim_and_stop_never_claims_a_known_cancelled_invocation,
        "finish": bot_playback.test_concurrent_finish_and_stop_never_posts_after_a_stopped_lease,
        "approval": bot_playback.test_concurrent_approval_and_stop_cannot_reenable_unconsumed_execution,
    }[operation]
    test(bot_playback.fixture.__wrapped__(tmp_path))
