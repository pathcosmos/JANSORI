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
TWO_FILESYSTEM_MODULES={'test_tiered_checkpoint_retention.py','test_checkpoint_retention_audit.py','test_retention3_policy.py'}

def pytest_addoption(parser):
    parser.addoption('--run-original-integrations',action='store_true',default=False,help='Require locally restored original run manifests; not included in the source-only checkout')

def pytest_sessionstart(session):
    runs=ROOT/'runs'
    if runs.is_symlink():raise RuntimeError('Tests require a checkout-owned runs directory, not a symlink to production')
    runs.mkdir(exist_ok=True)

def pytest_collection_modifyitems(config,items):
    same_device=ROOT.stat().st_dev==Path(tempfile.gettempdir()).stat().st_dev
    for item in items:
        node=Path(str(item.path)).name+'::'+(item.cls.__name__+'::' if item.cls else '')+item.originalname
        if (item.name in PRODUCTION_TESTS or node in ORIGINAL_RUN_NODES) and not config.getoption('--run-original-integrations'):
            item.add_marker(pytest.mark.skip(reason='Original-run integration: requires excluded production dataset release/active training manifests; use --run-original-integrations only in a restored test copy'))
        if Path(str(item.path)).name in TWO_FILESYSTEM_MODULES and same_device:
            item.add_marker(pytest.mark.skip(reason='Retention safety requires two distinct filesystems; put TMPDIR on a separate device to exercise this CPU fixture'))
