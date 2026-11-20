"""Four-generation accounting; original contracts and absolute steps are immutable.

This only recomputes rank/source ledgers. It cannot certify a checkpoint,
authorize a launch or retire any payload. Trace/seal/restore gates are separate.
"""
from scripts.audit_extended_horizon import aggregate_restored_lineage
from scripts.audit_horizon_training import aggregate_suffix
from train.budget_horizon_contract import (TARGET_STEPS, BINDING_KEY,
    validate_candidate, validate_bound_candidate)


def aggregate_budget_lineage(*, original, current, restored, child,
        original_records, current_records, restored_records, child_records,
        extension_metadata, parent_metadata, through_step):
    boundary = parent_metadata['step']
    if parent_metadata['training_contract'] != restored:
        raise ValueError('Fourth-generation parent metadata differs')
    if type(through_step) is not int or not boundary <= through_step <= TARGET_STEPS:
        raise ValueError('50k audit interval outside parent/target')
    if len({c['id'] for c in (original, current, restored, child)}) != 4:
        raise ValueError('Four distinct execution contract identities required')
    if BINDING_KEY in child:
        validate_bound_candidate(parent_metadata, child, child[BINDING_KEY])
    else:
        validate_candidate(parent_metadata, child)
    prefix = aggregate_restored_lineage(original=original, current=current,
        restored=restored, original_records=original_records,
        current_records=current_records, restored_records=restored_records,
        boundary=10000, through_step=boundary, parent_metadata=extension_metadata)
    segments = list(prefix['segments'])
    if through_step == boundary:
        if child_records:
            raise ValueError('Unexpected child records before first 50k update')
    else:
        segments.append(aggregate_suffix(child_records, parent_step=boundary,
                                       through_step=through_step, contract=child))
    source_tokens = {s: 0 for s in original['source_order']}
    generations = {}
    committed = pairs = persisted = replayed = initializations = end = 0
    for segment in segments:
        if segment['start_step_exclusive'] != end:
            raise ValueError('Gap or overlap between four lineage segments')
        end = segment['through_step']
        committed += segment['committed_tokens']
        pairs += segment['committed_step_rank_pairs']
        persisted += segment['persisted_attempt_tokens_through_prefix']
        replayed += segment['replayed_tokens_through_prefix']
        initializations += segment['optimizer_initializations_excluded']
        if set(segment['source_tokens']) != set(source_tokens):
            raise ValueError('Lineage source coverage differs')
        for source in source_tokens:
            source_tokens[source] += segment['source_tokens'][source]
        if generations.keys() & segment['generation_sha256_by_step'].keys():
            raise ValueError('Overlapping generation receipts')
        generations.update(segment['generation_sha256_by_step'])
    if (end != through_step or pairs != through_step * 8
            or committed != through_step * 2097152 or sum(source_tokens.values()) != committed
            or persisted != committed + replayed):
        raise ValueError('Four-generation cumulative accounting differs')
    final_ranks = segments[-1]['successful_target_ranks'] if len(segments) == 4 else []
    if through_step == TARGET_STEPS and final_ranks != list(range(8)):
        raise ValueError('50k completion requires all eight successful rank receipts')
    return dict(status='passed_four_generation_ledger_accounting_only',
        through_step=through_step, target_steps=TARGET_STEPS, boundary=boundary,
        committed_tokens=committed, committed_step_rank_pairs=pairs,
        source_tokens=source_tokens, source_fractions={s:n/committed for s,n in source_tokens.items()},
        generation_sha256_by_step=generations, successful_target_ranks=final_ranks,
        persisted_attempt_tokens_through_prefix=persisted,
        replayed_tokens_through_prefix=replayed, optimizer_initializations_excluded=initializations,
        segments=segments, runtime_authorized=False, lineage_sealed=False,
        checkpoint_payload_verified=False, distributed_resume_verified=False,
        scope='Unique committed updates only; persisted tails and diagnostics excluded; unpersisted work unknown.')
