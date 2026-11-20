"""Compare complete state observations; never replace DCP/GPU/ledger acceptance."""
import math
import re

from common.data_contracts import identity
from train.horizon_probe_observer import MODES


def compare_observations(rows, *, parent_step, plan_id, world=8, require_cuda=True):
    if type(world) is not int or world<1 or (require_cuda and world!=8):
        raise ValueError('Production diagnostic comparison requires8ranks')
    indexed={}
    def state(value):
        if set(value)!={'model','optimizer','rng','device_type','identity'}:
            raise ValueError('Incomplete state observation')
        payload=dict(value);digest=payload.pop('identity')
        if identity(payload)!=digest:
            raise ValueError('State observation identity differs')
        if require_cuda and value['device_type']!='cuda':
            raise ValueError('CPU observation cannot prove actual8GPU restore')
        for name in ['model','optimizer','rng']:
            part=value[name]
            if (set(part)!={'sha256','tensors','global_elements','local_bytes'} or
                    not re.fullmatch('[0-9a-f]{64}',part['sha256']) or
                    any(type(part[k]) is not int or part[k]<1 for k in ['tensors','global_elements','local_bytes'])):
                raise ValueError('Incomplete model/optimizer/RNG coverage')
        if value['rng']['tensors']!=(2 if value['device_type']=='cuda' else 1):
            raise ValueError('Missing rank RNG coverage')
    for row in rows:
        mode,rank=row['mode'],row['rank']
        if (mode not in MODES or type(rank) is not int or not 0<=rank<world or
                (mode,rank) in indexed or row['world_size']!=world or row['plan_id']!=plan_id or
                row['parent_step']!=parent_step or row['success'] is not True or
                row['diagnostic_only'] is not True or row['main_committed_tokens_added']!=0):
            raise ValueError('Unexpected or duplicate diagnostic mode/rank identity')
        begin,end=MODES[mode]
        if (row['start_step']!=parent_step+begin or row['end_step']!=parent_step+end or
                row['initial']['step']!=parent_step+begin or
                [x['step'] for x in row['updates']]!=list(range(parent_step+begin+1,parent_step+end+1))):
            raise ValueError('Incomplete absolute-step diagnostic coverage')
        state(row['initial']['state'])
        for update in row['updates']:
            if type(update['lr']) not in (int,float) or not math.isfinite(update['lr']) or update['lr']<=0:
                raise ValueError('Invalid diagnostic learning rate')
            state(update['state'])
        indexed[mode,rank]=row
    if set(indexed)!={(m,r) for m in MODES for r in range(world)}:
        raise ValueError('Missing four-mode rank coverage')
    for rank in range(world):
        baseline,continuous,first,resumed=[indexed[m,rank] for m in ['baseline','continuous','first','resumed']]
        if baseline['initial']!=continuous['initial'] or continuous['initial']!=first['initial']:
            raise ValueError('Parent full-state restoration differs between modes')
        if baseline['updates']!=continuous['updates']:
            raise ValueError('Original/new horizon update trajectory differs')
        if continuous['updates']!=first['updates']+resumed['updates']:
            raise ValueError('Continuous/split state trajectory differs')
        if first['updates'][0]['state']!=resumed['initial']['state']:
            raise ValueError('Saved first update and restored second-mode state differ')
    return dict(status='passed_observation_comparison_not_complete_diagnostic',
                world_size=world,modes=sorted(MODES),compared_rank_updates=world*2,
                parent_states_equal=True,old_new_updates_equal=True,split_resume_states_equal=True,
                actual8gpu_acceptance=False,main_committed_tokens_added=0,
                remaining=['Authenticated execution/8GPU provenance','Fresh full DCP certification and independent parent-state comparison',
                           'Native source ledgers and input/LR contract validation'])
