"""Bounded state observation for real native horizon-resume diagnostics.

This module never launches a process or changes a training contract. The future
diagnostic controller must bind its SHA and require actual8GPU coverage. Small
CPU tests exercise the same hooks without being distributed acceptance proof.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import time

from common.data_contracts import identity
from scripts.tiered_checkpoint_retention import atomic_json, exact_path
from train.final_resume_probe import rng_state, verify_optimizer_step

MODES = {'baseline':(0,2), 'continuous':(0,2), 'first':(0,1), 'resumed':(1,2)}


def fingerprint(value):
    """Hash every state leaf, including DTensor placement and local shard bytes."""
    import torch
    digest = hashlib.sha256()
    tensors = elements = local_bytes = 0
    def tag(obj):
        raw=json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        digest.update(len(raw).to_bytes(8,'big'));digest.update(raw)
    def visit(item):
        nonlocal tensors,elements,local_bytes
        if isinstance(item,torch.Tensor):
            local=item.to_local() if hasattr(item,'to_local') else item
            if local.layout!=torch.strided:
                raise ValueError('Only dense strided diagnostic states are supported')
            layout=dict(kind='tensor',dtype=str(item.dtype),shape=list(item.shape),local_shape=list(local.shape))
            if hasattr(item,'placements'):
                layout.update(placements=[str(p) for p in item.placements],mesh=item.device_mesh.mesh.tolist())
            tag(layout)
            raw=local.detach().contiguous().cpu().reshape(-1).view(torch.uint8).numpy()
            digest.update(memoryview(raw))
            tensors+=1;elements+=item.numel();local_bytes+=raw.nbytes
        elif isinstance(item,dict):
            if any(not isinstance(k,str) for k in item):
                raise ValueError('State-dict diagnostic keys must be strings')
            tag(['dict',sorted(item)])
            for key in sorted(item):
                tag(key);visit(item[key])
        elif isinstance(item,(list,tuple)):
            tag(['tuple' if isinstance(item,tuple) else 'list',len(item)])
            for child in item:visit(child)
        elif item is None or type(item) in (str,int,float,bool):
            tag([type(item).__name__,item])
        else:
            raise ValueError('Unsupported diagnostic state leaf: '+type(item).__name__)
    visit(value)
    return dict(sha256=digest.hexdigest(),tensors=tensors,global_elements=elements,local_bytes=local_bytes)


def capture_state(model, optimizer, device, expected_step):
    """Capture full state hashes; explicitly reject observation consuming RNG."""
    import torch
    from torch.distributed.checkpoint.state_dict import get_state_dict
    verify_optimizer_step(optimizer,expected_step)
    before=rng_state(device)
    state,optim=get_state_dict(model,optimizer)
    result=dict(model=fingerprint(state),optimizer=fingerprint(optim),rng=fingerprint(before),
                device_type=torch.device(device).type)
    after=rng_state(device)
    if before.keys()!=after.keys() or any(not torch.equal(before[k],after[k]) for k in before):
        raise ValueError('Diagnostic state observation consumed RNG')
    result['identity']=identity(result)
    return result


class NativeStateObserver:
    """Observe the unchanged native trainer at restore/before/after update hooks."""
    def __init__(self, root, *, mode, rank, world, parent_step, config, plan_id, on_initial=None):
        if (mode not in MODES or type(world) is not int or world<1 or type(rank) is not int or
                not 0<=rank<world or type(parent_step) is not int or parent_step<1):
            raise ValueError('Invalid bounded diagnostic role/geometry')
        begin,end=MODES[mode]
        if config.get('stop_after')!=parent_step+end:
            raise ValueError('Diagnostic stop boundary differs from mode')
        if not isinstance(plan_id,str) or len(plan_id)!=64:
            raise ValueError('Diagnostic plan identity missing')
        self.path=exact_path(Path(root)/f'{mode}-rank{rank}.json')
        if self.path.exists():
            raise ValueError('Preserve existing diagnostic receipt; do not overwrite attempt')
        self.mode,self.rank,self.world=mode,rank,world
        self.parent_step,self.begin,self.end=parent_step,begin,end
        self.config=dict(config);self.plan_id=plan_id;self.on_initial=on_initial
        self.model=self.optimizer=None
        self.rows=[];self.initial=None;self.pending=None;self.current=parent_step+begin
        self.started=time.monotonic()

    def persist(self, success=False, error=None):
        atomic_json(self.path,dict(kind='native_horizon_probe_observation',plan_id=self.plan_id,
            mode=self.mode,rank=self.rank,world_size=self.world,parent_step=self.parent_step,
            start_step=self.parent_step+self.begin,end_step=self.parent_step+self.end,
            initial=self.initial,updates=self.rows,success=success,error=error,
            elapsed_seconds=time.monotonic()-self.started,diagnostic_only=True,
            actual8gpu_acceptance=False,main_committed_tokens_added=0))

    def bind(self, model, optimizer):
        if self.model is not None:
            raise ValueError('Diagnostic model was loaded more than once')
        self.model,self.optimizer=model,optimizer

    def before(self, step, config, lr):
        from train.runtime import RESUME_KEYS
        if (self.model is None or self.pending is not None or step!=self.current or
                not self.parent_step+self.begin<=step<self.parent_step+self.end or
                any(config[k]!=self.config[k] for k in RESUME_KEYS)):
            raise ValueError('Unexpected native diagnostic update/configuration')
        device=next(self.model.parameters()).device
        if self.initial is None:
            self.initial=dict(step=step,state=capture_state(self.model,self.optimizer,device,step))
            self.persist()
            if self.on_initial:
                # E.g. a full restored-parent DCP copy for independent CPU comparison.
                self.on_initial(self.model,self.optimizer,step)
                if capture_state(self.model,self.optimizer,device,step)!=self.initial['state']:
                    raise ValueError('Initial diagnostic checkpoint callback changed state/RNG')
        self.pending=dict(step=step+1,lr=lr)

    def after(self, optimizer):
        if optimizer is not self.optimizer or self.pending is None:
            raise ValueError('Unexpected optimizer update outside bounded native probe')
        state=capture_state(self.model,optimizer,next(self.model.parameters()).device,self.pending['step'])
        self.rows.append(dict(self.pending,state=state))
        self.current=self.pending['step'];self.pending=None;self.persist()

    def finish(self):
        expected=list(range(self.parent_step+self.begin+1,self.parent_step+self.end+1))
        if self.initial is None or self.pending is not None or [r['step'] for r in self.rows]!=expected:
            raise ValueError('Diagnostic did not complete the exact requested updates')
        self.persist(success=True)


@contextmanager
def observe_native(trainer, observer):
    """Install before invoking monitored.worker; native optimization is unchanged.

    Monitored worker wraps AdamW.step again to commit its original source ledger.
    Pre-load optimizer initialization passes through and is not counted. Hooks
    are restored on every exit; no subprocess or parent-run signaling occurs.
    """
    import torch
    original_set=trainer.set_state_dict
    original_lr=trainer.lr_at
    original_step=torch.optim.AdamW.step
    def restore(model,optimizer,*args,**kwargs):
        result=original_set(model,optimizer,*args,**kwargs)
        observer.bind(model,optimizer)
        return result
    def lr_at(step,config):
        lr=original_lr(step,config)
        observer.before(step,config,lr)
        return lr
    def step(optimizer,*args,**kwargs):
        result=original_step(optimizer,*args,**kwargs)
        if observer.model is not None:
            observer.after(optimizer)
        return result
    trainer.set_state_dict,trainer.lr_at,torch.optim.AdamW.step=restore,lr_at,step
    try:
        yield observer
        observer.finish()
    except BaseException as exc:
        observer.persist(error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        trainer.set_state_dict,trainer.lr_at,torch.optim.AdamW.step=original_set,original_lr,original_step
