"""Read-only 35k -> original 57k proposal. Never authorizes execution.

Preserves the existing 8k ancestry binding verbatim. A later execution bridge
must add a distinct binding, seal all committed ledger segments, and prove real
distributed resume equivalence before this candidate can be used by a worker.
"""
from copy import deepcopy

from common.data_contracts import identity
from train.horizon_transition import _verified_contract, _integer, _schedule, _require


def validate_extension(parent, child, original, committed_step):
    p, c, o = map(_verified_contract, (parent, child, original))
    step = _integer(committed_step, 'committed step')
    _require(p['config']['max_steps'] == 35000 and
             c['config']['max_steps'] == o['config']['max_steps'] == 57000,
             'Expected exact35k to original57k restoration')
    ancestry = p.get('horizon_transition', {})
    _require(ancestry.get('parent_contract_id') == original['id'] and
             ancestry.get('parent_step') == 8000, 'Original8k ancestry differs')
    _require(8000 < step <= min(_schedule(p['config']), _schedule(c['config'])),
             'Prefix entered changed decay or precedes the child segment')
    _require(p['world_size'] == c['world_size'] == o['world_size'] == 8,
             'Expected8rank contracts')
    old_cfg = deepcopy(p['config']); old_cfg['max_steps'] = 57000
    _require(old_cfg == o['config'], 'Original optimizer/input geometry differs')
    for key in ('files', 'mix', 'source_order', 'holdout', 'execution'):
        _require(p[key] == o[key], 'Original input/execution differs: ' + key)
    pe, ce, oe = [v['monitored_evaluation'] for v in (p, c, o)]
    for key in ('probe_sha256', 'prompts_sha256'):
        _require(pe[key] == oe[key], 'Original evaluation identity differs')
    for ev, end in ((pe, 35000), (ce, 57000), (oe, 57000)):
        s = ev['generation_steps']
        _require(isinstance(s, list) and bool(s) and
                 all(type(x) is int and 0 < x <= end for x in s) and
                 s == sorted(set(s)) and s[-1] == end, 'Invalid generation schedule')
    _require(ce['generation_steps'] == oe['generation_steps'],
             'Must restore the exact original generation schedule')
    _require([x for x in pe['generation_steps'] if x <= step] ==
             [x for x in ce['generation_steps'] if x <= step],
             'Already committed generation schedule differs')
    c['config']['max_steps'] = 35000
    ce['generation_steps'] = pe['generation_steps']
    _require(c == p, 'Unreviewed contract change; ancestry/code must remain intact')
    return dict(status='extension_candidate_only_not_executable',
                parent_contract_id=parent['id'], original_contract_id=original['id'],
                candidate_contract_id=child['id'], parent_step=step,
                target_steps=57000, decay_start_step=48450,
                total_tokens=57000*p['config']['global_tokens'],
                committed_tokens=step*p['config']['global_tokens'],
                prefix_lr_schedule_identical=True, runtime_applied=False,
                checkpoint_payload_verified=False, distributed_resume_verified=False,
                lineage_sealed=False)


def build_extension_candidate(metadata, original):
    parent = metadata['training_contract']; p = _verified_contract(parent)
    _require(metadata.get('world_size') == p['world_size'] == 8, 'Metadata world mismatch')
    _require(metadata.get('sampler_version') == p['config']['sampler_version'],
             'Metadata sampler mismatch')
    _require(metadata.get('tokens_per_step') == p['config']['global_tokens'],
             'Metadata token geometry mismatch')
    _require(isinstance(metadata.get('config'), dict) and
             all(metadata['config'].get(k) == v for k, v in p['config'].items()),
             'Metadata config mismatch')
    candidate = deepcopy(p)
    candidate['config']['max_steps'] = 57000
    candidate['monitored_evaluation']['generation_steps'] = deepcopy(
        _verified_contract(original)['monitored_evaluation']['generation_steps'])
    candidate['id'] = identity(candidate)
    return candidate, validate_extension(parent, candidate, original, metadata['step'])
