"""Read-only three-generation ledger accounting; not an execution admission.

Records keep their original contract IDs and absolute steps. Payload certification,
trace validation, immutable lineage publication and distributed resume are separate
gates. A passing result here must never authorize a worker or checkpoint deletion.
"""
from scripts.audit_main_training import aggregate, integer
from scripts.audit_horizon_training import aggregate_suffix
from train.extended_horizon_transition import validate_extension


def aggregate_restored_lineage(*, original, current, restored, original_records,
                               current_records, restored_records, boundary,
                               through_step, parent_metadata=None):
    if parent_metadata is None:
        validate_extension(current, restored, original, boundary)
    else:
        from train.extended_horizon_worker_contract import validate_bound_candidate
        if parent_metadata['training_contract'] != current or parent_metadata['step'] != boundary:
            raise ValueError('Bound accounting parent metadata differs')
        validate_bound_candidate(parent_metadata, original, restored,
                                 restored['extended_horizon_transition'])
    integer(through_step, 'through step')
    if not boundary <= through_step <= 57000:
        raise ValueError('Restored interval outside boundary/target')
    if len({original['id'], current['id'], restored['id']}) != 3:
        raise ValueError('Three distinct execution identities required')
    cfg = original['config']
    if cfg['global_tokens'] != 2097152 or cfg['seq_len'] != 4096:
        raise ValueError('Expected approved3B token geometry')
    # Each underlying validator checks every rank and repeated source choices;
    # persisted tails beyond the selected boundary are excluded, never relabelled.
    first = aggregate(original_records, world=8, through_step=8000,
        target_steps=57000, seq_len=cfg['seq_len'], global_tokens=cfg['global_tokens'],
        sources=original['source_order'],
        generation_steps=original['monitored_evaluation']['generation_steps'],
        contract_id=original['id'])
    first['start_step_exclusive'] = 0
    middle = aggregate_suffix(current_records, parent_step=8000,
                              through_step=boundary, contract=current)
    segments = [first, middle]
    if through_step == boundary:
        if restored_records:
            raise ValueError('Unexpected restored records before first child update')
    else:
        segments.append(aggregate_suffix(restored_records, parent_step=boundary,
                                         through_step=through_step, contract=restored))
    tokens = {s: 0 for s in original['source_order']}
    generations = {}
    pairs = committed = persisted = replayed = initializations = 0
    end = 0
    for segment in segments:
        if segment['start_step_exclusive'] != end:
            raise ValueError('Gap or overlap between lineage segments')
        end = segment['through_step']
        pairs += segment['committed_step_rank_pairs']
        committed += segment['committed_tokens']
        persisted += segment['persisted_attempt_tokens_through_prefix']
        replayed += segment['replayed_tokens_through_prefix']
        initializations += segment['optimizer_initializations_excluded']
        for source in tokens:
            tokens[source] += segment['source_tokens'][source]
        if generations.keys() & segment['generation_sha256_by_step'].keys():
            raise ValueError('Overlapping generation receipts')
        generations.update(segment['generation_sha256_by_step'])
    if (end != through_step or pairs != 8*through_step or
            committed != cfg['global_tokens']*through_step or
            sum(tokens.values()) != committed or persisted != committed+replayed):
        raise ValueError('Combined committed/replayed token accounting differs')
    final_ranks = segments[-1]['successful_target_ranks'] if len(segments) == 3 else []
    if through_step == 57000 and final_ranks != list(range(8)):
        raise ValueError('Final target requires successful receipts from all8ranks')
    return dict(status='passed_ledger_accounting_only', through_step=through_step,
        boundary=boundary, target_steps=57000, committed_tokens=committed,
        committed_step_rank_pairs=pairs, source_tokens=tokens,
        source_fractions={s:n/committed for s,n in tokens.items()},
        generation_sha256_by_step=generations, successful_target_ranks=final_ranks,
        persisted_attempt_tokens_through_prefix=persisted,
        replayed_tokens_through_prefix=replayed,
        optimizer_initializations_excluded=initializations,
        segments=segments, lineage_sealed=False, runtime_authorized=False,
        checkpoint_payload_verified=False, distributed_resume_verified=False,
        scope='Exact-contract ledgers; persisted tails and diagnostic work excluded; '
              'unpersisted work unknown. Trace/payload/lineage-seal checks required separately.')
