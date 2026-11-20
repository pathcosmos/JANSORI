"""Strict, read-only horizon transition planning; not a resume authorization.

The live trainer does not import this module. A candidate produced here must
still acquire a sealed parent checkpoint, lineage/ledger proof, an independently
validated worker/controller bundle, and an actual distributed resume test.
"""
from copy import deepcopy
import math

from common.data_contracts import identity


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _integer(value, label, minimum=1):
    _require(type(value) is int and value >= minimum, f'Invalid {label}')
    return value


def _verified_contract(contract):
    _require(isinstance(contract, dict), 'Missing training contract')
    value = deepcopy(contract)
    digest = value.pop('id', None)
    _require(digest == identity(value), 'Training contract identity mismatch')
    for key in ('config', 'files', 'mix', 'source_order', 'holdout', 'code',
                'execution', 'monitored_evaluation', 'world_size'):
        _require(key in value, f'Missing contract field: {key}')
    return value


def _schedule(config):
    target = _integer(config['max_steps'], 'max_steps')
    warmup = _integer(config['warmup'], 'warmup')
    for key in ('lr', 'min_lr_ratio', 'decay_frac'):
        v = config[key]
        _require(type(v) in (float, int) and math.isfinite(v), f'Invalid {key}')
    _require(config['lr'] > 0, 'Invalid peak LR')
    _require(0 < config['min_lr_ratio'] <= 1, 'Invalid minimum LR ratio')
    _require(0 < config['decay_frac'] < 1, 'Invalid decay fraction')
    start = int(target * (1 - config['decay_frac']))
    _require(warmup <= start < target, 'Warmup overlaps decay or empty decay')
    return start


def validate_transition(parent, child, committed_step):
    """Allow only a shortened horizon and truncation of future generations.

    Strict equality retains all input hashes, optimizer geometry, implementation,
    execution semantics and previous evaluation identity. Code migration is NOT
    allowed by this validator and needs a separately validated future layer.
    Returns evidence about schedule equivalence, never a checkpoint certificate.
    """
    p, c = _verified_contract(parent), _verified_contract(child)
    step = _integer(committed_step, 'committed step')
    old_target = _integer(p['config']['max_steps'], 'parent target')
    target = _integer(c['config']['max_steps'], 'child target')
    _require(step < target < old_target, 'Expected a future, shortened horizon')
    old_decay, new_decay = _schedule(p['config']), _schedule(c['config'])
    _require(step <= min(old_decay, new_decay), 'Committed prefix already entered changed decay')
    old_schedule = p['monitored_evaluation']['generation_steps']
    new_schedule = c['monitored_evaluation']['generation_steps']
    for schedule, end in ((old_schedule, old_target), (new_schedule, target)):
        _require(isinstance(schedule, list) and bool(schedule), 'Missing generation schedule')
        _require(all(type(s) is int and 0 < s <= end for s in schedule), 'Invalid generation step')
        _require(schedule == sorted(set(schedule)), 'Unordered or duplicate generation schedule')
        _require(schedule[-1] == end, 'Final generation is required')
    _require(new_schedule == sorted(set([s for s in old_schedule if s <= target] + [target])),
             'Only future generation truncation and final generation are allowed')
    # Canonicalize ONLY these two reviewed differences; fail closed on all others.
    c['config']['max_steps'] = old_target
    c['monitored_evaluation']['generation_steps'] = old_schedule
    _require(c == p, 'Unreviewed data/config/world/code/evaluation change')
    return dict(parent_contract_id=parent['id'], candidate_contract_id=child['id'],
                parent_target_steps=old_target, target_steps=target,
                committed_prefix_step=step, decay_start_step=new_decay,
                parent_decay_start_step=old_decay, prefix_lr_schedule_identical=True,
                runtime_applied=False, checkpoint_payload_verified=False,
                distributed_resume_verified=False)


def build_candidate(metadata, target_steps):
    """Construct a proposal from immutable metadata without rewriting that metadata."""
    _require(isinstance(metadata, dict), 'Missing checkpoint metadata')
    parent = metadata.get('training_contract')
    p = _verified_contract(parent)
    target = _integer(target_steps, 'target steps')
    step = _integer(metadata.get('step'), 'metadata step')
    _require(metadata.get('world_size') == p['world_size'] == 8, 'Expected 8-rank metadata')
    _require(metadata.get('sampler_version') == p['config']['sampler_version'], 'Sampler metadata mismatch')
    config = metadata.get('config')
    _require(isinstance(config, dict) and all(config.get(k) == v for k, v in p['config'].items()),
             'Metadata config disagrees with training identity')
    _require(metadata.get('tokens_per_step') == p['config']['global_tokens'], 'Token geometry mismatch')
    candidate = deepcopy(p)
    candidate['config']['max_steps'] = target
    candidate['monitored_evaluation']['generation_steps'] = sorted(set(
        [s for s in p['monitored_evaluation']['generation_steps'] if s <= target] + [target]))
    candidate['id'] = identity(candidate)
    evidence = validate_transition(parent, candidate, step)
    evidence.update(status='candidate_only_not_executable', total_tokens=target * metadata['tokens_per_step'],
                    committed_tokens=step * metadata['tokens_per_step'])
    return candidate, evidence
