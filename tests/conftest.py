"""Public checkout support. Optional original-run integrations are explicitly reported."""
import os
from pathlib import Path
import tempfile
import pytest
ROOT=Path(__file__).resolve().parents[1]
PRODUCTION_TESTS={
 'test_original_validated_copy_settings',
 'test_original_3b_optimizer_metadata',
 'test_actual_main_marker_without_target_cannot_pass_final_gate',
 'test_single_repository_release_download_paths_and_retry',
 'test_release_assembly_and_retry_keep_13_languages_and_pins',
 'test_final_native_package_retains_exact_732_inputs_and_reuses_release_on_retry',
 'test_current_prepared_plan_bindings_and_worker_tmpdir_admission',
 'test_actual_parent_paths_protected_and_current_bindings_preparable',
 'test_sigterm_stops_owned_worker_only_and_preserves_incomplete_evidence',
}
ORIGINAL_RUN_NODES={  # v0.2.0: tests added from the private repository that read excluded run records (runs/, artifacts/, tokenizer/candidate_v1)
 'test_budget_final_dependency_scope.py::test_exact_three_protected_and_local_paths',
 'test_budget_final_dependency_scope.py::test_wrong_or_aliased_scopes_are_rejected',
 'test_budget_hf_source.py::test_running_production_cannot_publish_with_deferred_budget',
 'test_budget_transition_preflight.py::test_current_source_four_owner_capacity_without_double_reserving_or_mutation',
 'test_budget_transition_preflight.py::test_exact_waiting_controllers_and_busy_child_rejection',
 'test_completed_storage_export.py::test_vllm_and_serving_propagate_the_same_storage_reference',
 'test_final_vllm_controller.py::test_actual_preparation_contract_and_live_export_wait',
 'test_final_vllm_controller.py::test_independent_runtime_review_binds_execution_and_outputs',
 'test_hf_export.py::test_actual_final_tokenizer_roundtrip',
 'test_hf_export_controller.py::test_independent_review_accepts_bound_fixture_only_and_detects_mutation',
 'test_hf_export_controller.py::test_independent_review_rejects_numerical_coverage_even_with_rehashed_receipts',
 'test_hf_export_controller.py::test_independent_review_rejects_supervised_identity_change',
 'test_hf_final_source.py::test_actual_current57k_run_is_not_final35k_source',
 'test_hf_publication.py::test_actual_fixed_six_prompts_full_context_and_final_tokenizer',
 'test_hf_publication.py::test_existing_destination_and_cross_filesystem_are_never_replaced',
 'test_hf_publication.py::test_real_hf_validation_publication_and_independent_reopen',
 'test_independent_followup.py::test_modified_command_plan_not_accepted_even_with_rewritten_marker',
 'test_independent_followup.py::test_prepare_binds_code_and_restored_paths_without_stage_outputs',
 'test_independent_hf_source.py::test_vllm_and_api_contracts_preserve_explicit_reference',
 'test_launch_budget_observer_v2.py::test_changed_scope_budget_or_evidence_rejected',
 'test_launch_budget_observer_v2.py::test_exact_reviewed_plan_has_one_readonly_observer_command',
 'test_restored_hf_controller.py::test_independent_review_accepts_bound_fixture_only_and_detects_mutation',
 'test_restored_hf_controller.py::test_independent_review_rejects_numerical_coverage_even_with_rehashed_receipts',
 'test_restored_hf_controller.py::test_independent_review_rejects_supervised_identity_change',
 'test_restored_hf_controller.py::test_publication_rejects_relabelled_original_ledger',
 'test_restored_hf_source.py::test_running_production_cannot_publish_with_deferred_budget',
 'test_restored_serving.py::test_independent_final_review_with_synthetic_gpu_source',
 'test_restored_serving.py::test_owned_cpu_http_lifecycle',
 'test_restored_serving.py::test_process_and_socket_substitution_rejected',
 'test_restored_serving.py::test_resource_exception_cleans_owned_command',
 'test_restored_serving.py::test_wrong_api_preserved_server_stopped',
 'test_restored_vllm_controller.py::test_independent_runtime_review_binds_execution_and_outputs',
 'test_restored_vllm_controller.py::test_new_runtime_binds_actual_environment_without_export_launch',
 'test_runtime_package.py::test_actual_tiny_hf_publisher_compatibility',
 'test_serving_process.py::test_actual_dependency_live_and_incomplete_source_gate',
 'test_serving_process.py::test_independent_final_review_with_synthetic_gpu_source',
 'test_serving_process.py::test_owned_cpu_http_lifecycle',
 'test_serving_process.py::test_process_and_socket_substitution_rejected',
 'test_serving_process.py::test_resource_exception_cleans_owned_command',
 'test_serving_process.py::test_wrong_api_preserved_server_stopped',
 'test_serving_validation.py::test_actual_chunked_http_and_truncation',
 'test_serving_validation.py::test_client_budget_timeout_and_guard',
 'test_serving_validation.py::test_corrupt_completion_rejected',
 'test_serving_validation.py::test_corrupt_stream_order_rejected',
 'test_serving_validation.py::test_eos_is_not_length_or_natural_quality_proof',
 'test_serving_validation.py::test_invalid_timing_not_throughput',
 'test_serving_validation.py::test_real_http_fixture_full_suite',
}
HOST_REQUIREMENT_NODES={  # v0.2.0: added tests that need host conditions GitHub-hosted runners lack (found in PR CI)
 'test_budget_final_isolation_guard.py::test_real_open_file_blocks_guard_until_private_reader_exits':('proc',),
 'test_budget_horizon_retention.py::BudgetRetentionTests::test_copy_failure_preserves_nfs_and_restarts_same_transaction':('two_fs',),
 'test_budget_horizon_retention.py::BudgetRetentionTests::test_four_owners_global_latest3_local15_and_protected_evidence':('two_fs',),
 'test_budget_horizon_retention.py::BudgetRetentionTests::test_interrupted_retirement_recovers_and_local_loss_stays_optional':('two_fs',),
 'test_budget_horizon_retention.py::BudgetRetentionTests::test_pressure_evicts_oldest_local_only':('two_fs',),
 'test_budget_horizon_retention.py::BudgetRetentionTests::test_shared_capacity_rejects_overlap_nfs_shortage_and_preserves_files':('two_fs',),
 'test_budget_horizon_retention.py::BudgetRetentionTests::test_uncertified_and_partial_new_snapshots_block_eviction':('two_fs',),
 'test_budget_horizon_retention.py::BudgetRetentionTests::test_wrong_interval_owner_and_authorization_rejected':('two_fs',),
 'test_budget_retention_audit.py::RestoredRetentionAuditTests::test_corrupted_payload_and_final_without_full_sha_rejected':('two_fs',),
 'test_budget_retention_audit.py::RestoredRetentionAuditTests::test_empty_new_branch_at_boundary_has_only_three_nfs_resume_candidates':('two_fs',),
 'test_budget_retention_audit.py::RestoredRetentionAuditTests::test_fresh_content_all_four_owners_is_read_only':('two_fs',),
 'test_budget_retention_audit.py::RestoredRetentionAuditTests::test_local_loss_is_optional_and_no_namespace_recreation':('two_fs',),
 'test_budget_retention_audit.py::RestoredRetentionAuditTests::test_local_only_latest_does_not_count_as_nfs_resume':('two_fs',),
 'test_budget_retention_audit.py::RestoredRetentionAuditTests::test_partial_staging_and_missing_schedule_fail_without_repair':('two_fs',),
 'test_extended_horizon_retention.py::ExtendedRetentionTests::test_copy_failure_preserves_nfs_and_restarts_same_transaction':('two_fs',),
 'test_extended_horizon_retention.py::ExtendedRetentionTests::test_interrupted_retirement_recovers_and_local_loss_stays_optional':('two_fs',),
 'test_extended_horizon_retention.py::ExtendedRetentionTests::test_pressure_evicts_oldest_local_only':('two_fs',),
 'test_extended_horizon_retention.py::ExtendedRetentionTests::test_shared_capacity_rejects_overlap_nfs_shortage_and_preserves_files':('two_fs',),
 'test_extended_horizon_retention.py::ExtendedRetentionTests::test_three_owners_global_latest3_local15_and_protected_evidence':('two_fs',),
 'test_extended_horizon_retention.py::ExtendedRetentionTests::test_uncertified_and_partial_new_snapshots_block_eviction':('two_fs',),
 'test_extended_horizon_retention.py::ExtendedRetentionTests::test_wrong_interval_owner_and_authorization_rejected':('two_fs',),
 'test_extended_observer_cadence.py::test_slow_resource_sample_does_not_add_a_poll_interval':('rss',),
 'test_final_horizon_resume_controller.py::FinalHorizonControllerTests::test_exact_shared_storage_budget_and_memory_caps':('two_fs',),
 'test_historical_archive_retirement.py::test_live_reader_blocks_retirement':('proc',),
 'test_horizon_boundary_integration.py::BoundaryIntegrationTests::test_capacity_failure_before_signals_preserves_original':('disk',),
 'test_horizon_boundary_integration.py::BoundaryIntegrationTests::test_changed_bound_input_is_rejected_before_namespace_or_signal':('disk',),
 'test_horizon_boundary_integration.py::BoundaryIntegrationTests::test_copy_then_seal_with_actual_cpu_process_termination':('disk',),
 'test_horizon_boundary_integration.py::BoundaryIntegrationTests::test_payload_corruption_fails_real_sha_before_original_termination':('disk',),
 'test_horizon_boundary_integration.py::BoundaryIntegrationTests::test_post_termination_seal_failure_keeps_verified_copy_and_original':('disk',),
 'test_horizon_boundary_integration.py::BoundaryIntegrationTests::test_unsettled_retention_never_copies_or_terminates':('disk',),
 'test_horizon_handoff_seal.py::HandoffSealTests::test_changed_source_after_preparation_rejected':('disk',),
 'test_horizon_handoff_seal.py::HandoffSealTests::test_foreign_partial_metadata_stage_is_preserved':('disk',),
 'test_horizon_handoff_seal.py::HandoffSealTests::test_foreign_residue_is_preserved':('disk',),
 'test_horizon_handoff_seal.py::HandoffSealTests::test_interrupted_lineage_publication_resumes_same_transaction':('disk',),
 'test_horizon_handoff_seal.py::HandoffSealTests::test_live_captured_process_rejected_before_publication':('disk',),
 'test_horizon_handoff_seal.py::HandoffSealTests::test_new_unlisted_process_owning_source_is_rejected':('disk',),
 'test_horizon_handoff_seal.py::HandoffSealTests::test_protected_payload_modified_after_copy_is_rejected':('disk',),
 'test_horizon_handoff_seal.py::HandoffSealTests::test_rehashed_trace_still_must_equal_original_source':('disk',),
 'test_horizon_handoff_seal.py::HandoffSealTests::test_seal_retry_and_worker_diagnostic_admission':('disk',),
 'test_horizon_handoff_seal.py::HandoffSealTests::test_wrong_owner_rejected':('disk',),
 'test_horizon_probe_collection.py::HorizonCollectionTests::test_launcher_handle_mismatch_preserves_failed_evidence':('disk',),
 'test_horizon_probe_collection.py::HorizonCollectionTests::test_missing_certificate_or_failed_certifier_phase_refused':('disk',),
 'test_horizon_probe_collection.py::HorizonCollectionTests::test_missing_rank_execution_receipt_is_not_acceptance':('disk',),
 'test_horizon_probe_collection.py::HorizonCollectionTests::test_native_training_contract_must_match_exact_candidate':('disk',),
 'test_horizon_probe_collection.py::HorizonCollectionTests::test_partial_independent_comparison_is_not_complete':('disk',),
 'test_horizon_probe_collection.py::HorizonCollectionTests::test_rehashed_changed_optimizer_observation_fails_comparison':('disk',),
 'test_horizon_probe_collection.py::HorizonCollectionTests::test_resume_must_use_the_fresh_intermediate_certificate':('disk',),
 'test_horizon_probe_collection.py::HorizonCollectionTests::test_same_size_protected_payload_change_refused':('disk',),
 'test_horizon_probe_collection.py::HorizonCollectionTests::test_sampler_coverage_must_match_real_global_batch':('disk',),
 'test_horizon_probe_collection.py::HorizonCollectionTests::test_synthetic_evidence_relationships_collect_without_adding_main_tokens':('disk',),
 'test_horizon_retention.py::GlobalRetentionTests::test_capacity_eviction_uses_oldest_across_both_local_namespaces':('two_fs',),
 'test_horizon_retention.py::GlobalRetentionTests::test_changed_authorization_and_crossed_contract_boundary_rejected':('two_fs',),
 'test_horizon_retention.py::GlobalRetentionTests::test_copy_failure_leaves_original_and_retry_recovers_same_owner':('two_fs',),
 'test_horizon_retention.py::GlobalRetentionTests::test_global_latest3_local15_across_contracts_and_owners':('two_fs',),
 'test_horizon_retention.py::GlobalRetentionTests::test_local_loss_does_not_become_resume_source_or_reconstruct_history':('two_fs',),
 'test_horizon_retention.py::GlobalRetentionTests::test_partial_nfs_retirement_recovers_with_global_latest_protection':('two_fs',),
 'test_horizon_retention.py::GlobalRetentionTests::test_uncertified_new_copy_blocks_eviction':('two_fs',),
 'test_horizon_retention_audit.py::HorizonRetentionAuditTests::test_final_requires_full_sha_and_snapshot_tracks_lost_namespace':('two_fs',),
 'test_horizon_retention_audit.py::HorizonRetentionAuditTests::test_global3_15_fresh_dcp_hashes_no_repair_no_mutation':('two_fs',),
 'test_horizon_retention_audit.py::HorizonRetentionAuditTests::test_local_only_final_cannot_supply_nfs_resume':('two_fs',),
 'test_horizon_retention_audit.py::HorizonRetentionAuditTests::test_missing_old_local_namespace_is_optional_and_never_recreated':('two_fs',),
 'test_horizon_retention_audit.py::HorizonRetentionAuditTests::test_payload_corruption_and_crossed_certificate_identity_fail':('two_fs',),
 'test_horizon_retention_audit.py::HorizonRetentionAuditTests::test_unexplained_hole_and_unverified_staging_fail_without_repair':('two_fs',),
 'test_horizon_retention_audit.py::HorizonRetentionAuditTests::test_wrong_global_manager_paths_and_missing_schedule_are_rejected':('two_fs',),
 'test_horizon_supervision.py::ProtectedParentTests::test_copy_interruption_retains_stage_and_resumes':('disk',),
 'test_horizon_supervision.py::ProtectedParentTests::test_copy_publish_exact_reuse_and_source_unchanged':('disk',),
 'test_horizon_supervision.py::ProtectedParentTests::test_crash_after_publish_recovers_even_without_original':('disk',),
 'test_horizon_supervision.py::ProtectedParentTests::test_foreign_residue_and_wrong_owner_preserved':('disk',),
 'test_horizon_supervision.py::ProtectedParentTests::test_source_corruption_and_destination_tampering_fail_closed':('disk',),
 'test_independent_parent_retention.py::test_actual_copy_retirement_full_content_and_optional_local_loss':('proc',),
 'test_independent_parent_retention.py::test_controller_rechecks_real_receipts_stat_and_absence_after_worker':('proc',),
 'test_independent_parent_retention.py::test_missing_or_changed_retirement_proof_cannot_hide_extra_nfs_or_damage':('proc',),
 'test_independent_resume_admission.py::test_actual_live_process_refused_without_signalling':('two_fs',),
 'test_independent_resume_admission.py::test_changed_ancestry_capture_and_revision_refused':('two_fs',),
 'test_independent_resume_admission.py::test_existing_namespace_and_unreserved_scope_refused':('two_fs',),
 'test_independent_resume_admission.py::test_positive_file_admission_and_unavailable_local_parent_gate':('two_fs',),
 'test_independent_resume_admission.py::test_real_dac_prepare_spec_and_controller_gate':('proc',),
 'test_independent_resume_admission.py::test_summary_without_underlying_audit_evidence_refused':('two_fs',),
 'test_independent_resume_admission.py::test_wrong_segments_source_mutation_and_resource_caps':('two_fs',),
 'test_independent_resume_isolation.py::test_active_reader_and_live_diagnostic_prevent_mutation':('proc', 'two_fs'),
 'test_independent_resume_isolation.py::test_actual_restore_sigkill_can_finish_without_payload_changes':('proc',),
 'test_independent_resume_isolation.py::test_changed_permissions_and_replaced_root_refused':('proc',),
 'test_independent_resume_isolation.py::test_original_storage_validator_counts_hidden_bytes_and_hook_reverts':('two_fs',),
 'test_independent_resume_isolation.py::test_partial_enter_can_restore_without_overwrite':('proc', 'two_fs'),
 'test_independent_resume_isolation.py::test_partial_restore_is_idempotent':('proc',),
 'test_independent_resume_isolation.py::test_real_sigkill_after_directory_chmod_recovers':('proc', 'two_fs'),
 'test_independent_resume_isolation.py::test_real_two_filesystem_dac_roundtrip_and_exact_payload_stats':('proc', 'two_fs'),
 'test_prepare_horizon_probe.py::ProbePreparationTests::test_changed_code_or_missing_probe_completion_rejected':('disk',),
 'test_prepare_horizon_probe.py::ProbePreparationTests::test_live_parent_refused_before_any_diagnostic_write':('disk',),
 'test_prepare_horizon_probe.py::ProbePreparationTests::test_owned_partial_copy_retries_without_changing_parent':('disk',),
 'test_prepare_horizon_probe.py::ProbePreparationTests::test_real_copy_seal_admission_and_same_transaction_retry':('disk',),
 'test_prepare_horizon_probe.py::ProbePreparationTests::test_resume_spec_requires_fresh_full_cuda_certificate':('disk',),
 'test_prepare_horizon_probe.py::ProbePreparationTests::test_space_refusal_before_copy_and_changed_owner_after_partial':('disk',),
 'test_prepare_horizon_probe.py::ProbePreparationTests::test_unknown_root_or_changed_draft_preserves_residue':('disk',),
 'test_restored_final_resume_controller.py::FinalHorizonControllerTests::test_exact_shared_storage_budget_and_memory_caps':('two_fs',),
 'test_restored_retention_audit.py::RestoredRetentionAuditTests::test_corrupted_payload_and_final_without_full_sha_rejected':('two_fs',),
 'test_restored_retention_audit.py::RestoredRetentionAuditTests::test_empty_new_branch_at_boundary_has_only_three_nfs_resume_candidates':('two_fs',),
 'test_restored_retention_audit.py::RestoredRetentionAuditTests::test_fresh_content_all_three_owners_is_read_only':('two_fs',),
 'test_restored_retention_audit.py::RestoredRetentionAuditTests::test_local_loss_is_optional_and_no_namespace_recreation':('two_fs',),
 'test_restored_retention_audit.py::RestoredRetentionAuditTests::test_local_only_latest_does_not_count_as_nfs_resume':('two_fs',),
 'test_restored_retention_audit.py::RestoredRetentionAuditTests::test_partial_staging_and_missing_schedule_fail_without_repair':('two_fs',),
 'test_retire_independent_parents.py::test_actual_executor_archives_only_two_payloads_and_keeps_receipts':('proc',),
 'test_retire_independent_parents.py::test_attempt_change_during_copy_stops_before_nfs_retirement':('proc',),
 'test_retire_independent_parents.py::test_cancel_before_copy_preserves_both_sources':('proc',),
 'test_retire_independent_parents.py::test_copy_and_retirement_interruptions_preserve_and_resume':('proc',),
 'test_retire_independent_parents.py::test_original_metadata_change_or_unowned_residue_after_completion_fails_audit':('proc',),
 'test_retire_independent_parents.py::test_partial_optional_archive_loss_is_not_reported_as_verified':('proc',),
 'test_retire_independent_parents.py::test_refuse_unaccepted_dead_or_out_of_budget_retirement':('proc',),
 'test_retire_independent_probe_payloads.py::test_exact_four_states_retired_with_nfs_metadata_rng_preserved':('two_fs',),
 'test_retire_independent_probe_payloads.py::test_historical_evidence_change_during_retirement_stops_before_next_state':('two_fs',),
 'test_retire_independent_probe_payloads.py::test_interrupted_cleanup_resumes_only_same_approved_inventory':('two_fs',),
 'test_retire_independent_probe_payloads.py::test_missing_approval_or_unsafe_execution_leaves_payloads':('two_fs',),
 'test_retire_independent_probe_payloads.py::test_unknown_transaction_file_is_preserved':('two_fs',),
 'test_run_horizon_probe.py::HorizonControllerTests::test_changed_bound_input_rejected_before_execution_record':('disk',),
 'test_run_horizon_probe.py::HorizonControllerTests::test_exact_commands_and_expected_native_checkpoint_metadata':('disk',),
 'test_run_horizon_probe.py::HorizonControllerTests::test_failed_phase_preserves_evidence_and_never_runs_later_modes':('disk',),
 'test_run_horizon_probe.py::HorizonControllerTests::test_four_modes_and_certification_order_then_collect':('disk',),
 'test_run_horizon_probe.py::HorizonControllerTests::test_guard_enforces_memory_budget':('disk',),
 'test_run_horizon_probe.py::HorizonControllerTests::test_guard_enforces_total_elapsed_budget':('disk',),
 'test_run_horizon_probe.py::HorizonControllerTests::test_guard_refuses_foreign_gpu_without_signalling_it':('disk',),
}
TWO_FILESYSTEM_MODULES={'test_tiered_checkpoint_retention.py','test_checkpoint_retention_audit.py','test_retention3_policy.py'}

GIB=2**30
def _uninspectable_processes():
    """Processes whose open files cannot be listed (e.g. a root-owned PID 1 on hosted CI runners).
    Retirement/isolation guards fail closed on these, so their positive-path tests need a host without them."""
    bad=[]
    for proc in Path('/proc').glob('[0-9]*'):
        try:next(iter((proc/'fd').iterdir()),None)
        except FileNotFoundError:continue
        except PermissionError:
            try:comm=(proc/'comm').read_text().strip()
            except OSError:comm='?'
            if comm not in ('docker-init','ssh-agent'):bad.append(proc.name)
    return bad

def _host_limits(items):
    import shutil
    tmp=Path(tempfile.gettempdir());limits={}
    if ROOT.stat().st_dev==tmp.stat().st_dev:limits['two_fs']='checkout and TMPDIR are on the same filesystem'
    bad=_uninspectable_processes()
    if bad:limits['proc']='processes with uninspectable open files: '+','.join(bad[:5])
    free=shutil.disk_usage(tmp).free
    if free<210*GIB:limits['disk']=f'TMPDIR free space {free/GIB:.0f} GiB < 210 GiB (protected-parent reserve is 200 GiB)'
    if len(items)>50:limits['rss']='observer RSS budget is exceeded by a long pytest session; run this module alone'
    return limits

def pytest_addoption(parser):
    parser.addoption('--run-original-integrations',action='store_true',default=False,help='Require locally restored original run manifests; not included in the source-only checkout')

def pytest_sessionstart(session):
    runs=ROOT/'runs'
    if runs.is_symlink():raise RuntimeError('Tests require a checkout-owned runs directory, not a symlink to production')
    runs.mkdir(exist_ok=True)

def pytest_collection_modifyitems(config,items):
    same_device=ROOT.stat().st_dev==Path(tempfile.gettempdir()).stat().st_dev
    limits=_host_limits(items)
    for item in items:
        node=Path(str(item.path)).name+'::'+(item.cls.__name__+'::' if item.cls else '')+item.originalname
        if (item.name in PRODUCTION_TESTS or node in ORIGINAL_RUN_NODES) and not config.getoption('--run-original-integrations'):
            item.add_marker(pytest.mark.skip(reason='Original-run integration: requires excluded production dataset release/active training manifests; use --run-original-integrations only in a restored test copy'))
        unmet=[limits[r] for r in HOST_REQUIREMENT_NODES.get(node,()) if r in limits]
        if unmet:
            item.add_marker(pytest.mark.skip(reason='Host condition not met: '+'; '.join(unmet)))
        if Path(str(item.path)).name in TWO_FILESYSTEM_MODULES and same_device:
            item.add_marker(pytest.mark.skip(reason='Retention safety requires two distinct filesystems; put TMPDIR on a separate device to exercise this CPU fixture'))
