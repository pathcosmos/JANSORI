"""Strict fourth-generation intervals shared by final50k evidence consumers."""
import json
from common.data_contracts import resolve

TARGET_STEPS=50000


def intervals(boundary, *, checkpoints=False):
    if type(boundary) is not int or not 10000<boundary<42500 or boundary%500:
        raise ValueError('Budget boundary must be a certified500step before42500')
    starts=(0,8000,10000,boundary)
    ends=(8000,10000,boundary,TARGET_STEPS)
    return [(a+(500 if checkpoints else 0),b) for a,b in zip(starts,ends)]


def contract_intervals(contract, *, checkpoints=False):
    boundary=contract['budget_execution']['parent_step']
    definitions=contract['retention_branches']
    if (contract.get('target_steps')!=TARGET_STEPS or len(definitions)!=4
            or len({str(resolve(d['run'])) for d in definitions})!=4
            or [(d['first_step'],d['last_step']) for d in definitions]!=intervals(boundary,checkpoints=True)
            or resolve(definitions[-1]['run'])!=resolve(contract['main_run'])):
        raise ValueError('Exact four owner intervals required')
    return intervals(boundary,checkpoints=checkpoints)


def main_intervals(main, *, checkpoints=False):
    return contract_intervals(json.loads((resolve(main)/'contract.json').read_text()),checkpoints=checkpoints)


def bound_intervals(segments, *, checkpoints=False):
    if len(segments)!=4 or len({s['contract_id'] for s in segments})!=4:
        raise ValueError('Four distinct original identities required')
    expected=intervals(segments[2]['through_step'])
    if [(s['start_step_exclusive'],s['through_step']) for s in segments]!=expected:
        raise ValueError('Bound four-segment coverage differs')
    return intervals(segments[2]['through_step'],checkpoints=checkpoints)
