"""Versioned own-NFS entry around the unchanged57k numerical worker.

Requires separately validated/authorized runtime, actual independent8GPU
acceptance, exact owned launcher and shared fresh admission. No supervisor or
automatic launch is provided here; the existing live worker is unchanged.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import exact_path,atomic_json
from train.independent_runtime_contract import validate_attempt,launcher_identity,verify_consumption_rows
from train.independent_operational_admission import PreparedOperationalAdmission


def environment_rank():
    try:rank,local,world,local_world=[int(os.environ[k]) for k in ('RANK','LOCAL_RANK','WORLD_SIZE','LOCAL_WORLD_SIZE')]
    except (KeyError,ValueError) as exc:raise ValueError('Exact8GPU torchrun rank environment required') from exc
    if (not 0<=rank<8 or local!=rank or world!=8 or local_world!=8 or
            os.environ.get('CUDA_VISIBLE_DEVICES')!='0,1,2,3,4,5,6,7'):
        raise ValueError('Exact8GPU torchrun rank/device environment required')
    return rank


def worker(execution):
    execution=exact_path(resolve(execution));execution_sha=sha256_file(execution)
    rank=environment_rank();attempt,validated,binding=validate_attempt(execution)
    rc,mc,_=validated;main=resolve(attempt['main_run'])
    if os.environ.get('TMPDIR')!=mc['tiered_retention']['temp_root']:
        raise ValueError('Worker temporary directory differs from original contract')
    # Popen and durable handle publication cannot be atomic. Wait only for
    # this new attempt's handle, then validate actual parent identities.
    deadline=time.monotonic()+10
    while not (execution.parent/'launch/handle.json').exists():
        if time.monotonic()>=deadline:raise ValueError('Owned launcher receipt was not published')
        time.sleep(.05)
    launch=launcher_identity(execution,attempt)
    out=execution.parent/'consumption';own=out/f'rank{rank}.json'
    if own.exists():raise ValueError('Preserve existing rank consumption attempt')
    shared=attempt['shared_admission']
    admission=PreparedOperationalAdmission.from_shared(shared['path'],shared['sha256'],rank=rank,binding=binding)
    if (admission.root!=main or admission.contract!=mc or
            admission.candidate['id']!=attempt['training_contract_id']):
        raise ValueError('Worker shared admission differs from approved runtime')
    import torch
    import torch.distributed as dist
    def consumed(value):
        if (not dist.is_initialized() or dist.get_backend()!='nccl' or dist.get_world_size()!=8 or
                dist.get_rank()!=rank or torch.cuda.current_device()!=rank or
                sha256_file(execution)!=execution_sha):
            raise ValueError('Actual8GPU consumption execution differs')
        props=torch.cuda.get_device_properties(rank);uuid=getattr(props,'uuid',None)
        if uuid is None:raise ValueError('Actual GPU UUID unavailable')
        fresh=launcher_identity(execution,attempt)
        if fresh!=launch:raise ValueError('Launcher changed during own-state consumption')
        row=dict(rank=rank,local_rank=rank,world_size=8,backend='nccl',device_uuid=str(uuid),
            pid=value['pid'],start_ticks=value['start_ticks'],attempt=attempt['attempt'],
            runtime_contract_sha256=attempt['runtime_contract_sha256'],
            shared_admission_sha256=shared['sha256'],consumption=value,**launch)
        out.mkdir(exist_ok=True)
        if own.exists():raise ValueError('Consumption receipt already exists')
        atomic_json(own,row)
        # Every rank persists its full model/AdamW/RNG consumption before any
        # rank returns to the original training loop. Supervisor independently
        # verifies live rank identities before allowing checkpoint retirement.
        dist.barrier()
        if rank==0:
            rows=[json.loads((out/f'rank{r}.json').read_text()) for r in range(8)]
            result=verify_consumption_rows(attempt,rows,owner=launch['owner'])
            atomic_json(out/'all_ranks.json',result)
        dist.barrier()
        if sha256_file(execution)!=execution_sha:raise ValueError('Execution changed after consumption')
    with admission.operational_hooks(consumed) as original:
        original.worker(main,main/'main.json',attempt['attempt'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execution',required=True)
    worker(parser.parse_args().execution)
