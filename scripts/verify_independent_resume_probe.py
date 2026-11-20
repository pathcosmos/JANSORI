"""Exact diagnostic rank/token/trajectory accounting at an own-NFS boundary.

These checks alone are not actualGPU/source-provenance acceptance.
"""
import math
from train.independent_resume_probe import MODES,diagnostic_lr

def ledger_review(rows, *, parent_step, world, config, source_order, plan_id):
    indexed = {}
    for row in rows:
        mode, rank = row['mode'], row['rank']
        if mode not in MODES or type(rank) is not int or not 0 <= rank < world or (mode, rank) in indexed:
            raise ValueError('Diagnostic mode/rank duplicate or invalid')
        if (row['success'] is not True or row['plan_id'] != plan_id or
                row['parent_step'] != parent_step or row['diagnostic_only'] is not True):
            raise ValueError('Diagnostic rank completion identity differs')
        begin, end = MODES[mode]
        expected = list(range(parent_step+begin+1, parent_step+end+1))
        if [s['step'] for s in row['steps']] != expected or [s['step'] for s in row['metrics']] != expected:
            raise ValueError('Diagnostic update coverage differs')
        for step, metric in zip(row['steps'], row['metrics']):
            counts = step['source_sequences']
            if (not counts or set(counts)-set(source_order) or
                    any(type(n) is not int or n < 1 for n in counts.values()) or
                    sum(counts.values())*config['seq_len']*world != config['global_tokens']):
                raise ValueError('Diagnostic committed source geometry differs')
            if (any(not math.isfinite(metric[k]) for k in ['loss', 'gnorm', 'lr']) or
                    metric['loss'] < 0 or metric['gnorm'] < 0 or metric['lr'] != diagnostic_lr(config,metric['step']-1)):
                raise ValueError('Diagnostic finite metric/LR differs')
        indexed[mode, rank] = row
    if set(indexed) != {(m, r) for m in MODES for r in range(world)}:
        raise ValueError('Incomplete diagnostic rank coverage')
    totals = {s: 0 for s in source_order}
    for rank in range(world):
        continuous = indexed['continuous', rank]
        split = [indexed['first', rank], indexed['resumed', rank]]
        if continuous['steps'] != [s for row in split for s in row['steps']]:
            raise ValueError('Restored stateless sampler choices differ')
        comparable = lambda metric: {k: metric[k] for k in ['step', 'loss', 'gnorm', 'lr']}
        if [comparable(m) for m in continuous['metrics']] != [comparable(m) for row in split for m in row['metrics']]:
            raise ValueError('Continuous/resumed trajectory differs')
        for step in continuous['steps']:
            for source, n in step['source_sequences'].items():
                totals[source] += n*config['seq_len']
    for offset in range(2):
        if len({indexed['continuous', r]['metrics'][offset]['loss'] for r in range(world)}) != 1:
            raise ValueError('Reduced loss differs across ranks')
    return dict(source_tokens=totals, unique_diagnostic_tokens=2*config['global_tokens'],
                actual_diagnostic_committed_tokens=4*config['global_tokens'],
                main_committed_tokens_added=0, compared_rank_pairs=world*2,
                scope='Two diagnostic updates repeated across continuous/split trajectories, outside main token target.')

