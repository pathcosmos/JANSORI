"""Prepared lifecycle hooks for unchanged production numerical worker.

No launch or runtime authorization. Admission still needs a verified execution
contract and actual8GPU proof. A source remains guarded through model/optimizer
load AND successful rank RNG application; later retention may rotate it.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path

from common.data_contracts import resolve,sha256_file,identity
from train.independent_resume_bridge import PreparedIndependentAdmission,WORKER
from train.independent_child_resume import IndependentChildResume
from train.parent_checkpoint_resume import canonical
from train.independent_resume_bridge import BRIDGE_FILES


class ConsumedChildResume(IndependentChildResume):
    def __init__(self,*,rank,**kwargs):
        if type(rank) is not int or not 0<=rank<8:raise ValueError('Exact rank0..7 required')
        self.rank=rank;self.loaded=False;self.rng_read=False;self.consumed=False;self.cpu_rng_applied=False
        self.rng_cpu=None;self.rng_cuda=None
        super().__init__(**kwargs)

    def load(self,loader,*args,**kwargs):
        if self.loaded or self.consumed:raise ValueError('Own source can be loaded only once per attempt')
        result=super().load(loader,*args,**kwargs)
        self.loaded=True
        return result

    def validate(self,metadata,contract):
        if self.loaded or self.consumed:raise ValueError('Resume validation cannot restart a consumed attempt')
        return super().validate(metadata,contract)

    def read_rng(self,loader,path,*args,**kwargs):
        if (not self.loaded or self.rng_read or self.consumed or
                canonical(path)!=self.child/f'rng_rank{self.rank}.pt'):
            raise ValueError('RNG load requires exact admitted rank after DCP load')
        self.verify_child();value=loader(path,*args,**kwargs);self.verify_child()
        if not isinstance(value,dict) or set(value)!={'cpu','cuda'}:
            raise ValueError('Full CPU/CUDA rank RNG state required')
        self.rng_read=True;self.rng_cpu=value['cpu'];self.rng_cuda=value['cuda']
        return value

    def consumed_after_rng(self):
        if not self.loaded or not self.rng_read or not self.cpu_rng_applied or self.consumed:
            raise ValueError('Full model/optimizer and RNG consumption required')
        self.verify_child();self.consumed=True


class PreparedOperationalAdmission(PreparedIndependentAdmission):
    def __init__(self,root,revision_path,*,rank):
        self.rank=rank;self.adapter=None;self._stable_hashes=None
        if type(rank) is not int or not 0<=rank<8:raise ValueError('Exact rank0..7 required')
        super().__init__(root,revision_path)
        self._stable_hashes={p:sha256_file(Path(p)) for p in self.stats
            if Path(p)!=self.root/'training/training_contract.json'}
        self._operational_code={p:sha256_file(resolve(p)) for p in [
            'train/independent_operational_admission.py',*self.bridge_code]}
        self.check()

    def shared_record(self,binding):
        """Export one fresh controller review for exact attempt-bound reuse."""
        self.check()
        if self.adapter is not None or not isinstance(binding,dict) or not binding:
            raise ValueError('Unused admission and explicit consumer binding required')
        return dict(purpose='shared_own_nfs_admission_v1',binding=binding,
            production_adoption_authorized=False,root=str(self.root),revision_path=str(self.revision_path),
            contract=self.contract,config=self.config,receipt=self.receipt,proof=self.proof,candidate=self.candidate,
            child=str(self.child),child_proof=self.child_proof,child_stats=self.child_stats,review=self.review,
            source_stats=self.stats,source_sha256=self._stable_hashes,bridge_code=self.bridge_code,
            operational_code=self._operational_code)

    @classmethod
    def from_shared(cls,path,expected_sha256,*,rank,binding):
        """No payload hash replay; caller binds the receipt in its contract.

        Recheck exact source/stat/content/metadata/code coverage before installing
        hooks. This does not authorize a worker or accept a GPU validation.
        """
        from scripts.audit_main_training import verify_restart_metadata
        from scripts.tiered_checkpoint_retention import inventory
        from train.extended_horizon_worker_contract import bound_candidate
        from train.runtime import complete_checkpoints
        path=canonical(resolve(path))
        if type(rank) is not int or not 0<=rank<8:raise ValueError('Exact rank0..7 required')
        if path.stat().st_size>16*2**20 or sha256_file(path)!=expected_sha256:
            raise ValueError('Shared admission digest/size differs')
        data=json.loads(path.read_text())
        if (data['purpose']!='shared_own_nfs_admission_v1' or data['binding']!=binding or
                not isinstance(binding,dict) or not binding or data['production_adoption_authorized'] is not False):
            raise ValueError('Shared admission consumer binding differs')
        obj=cls.__new__(cls);obj.rank=rank;obj.adapter=None
        obj.root=canonical(data['root']);obj.revision_path=canonical(data['revision_path'])
        if path.stat().st_dev!=obj.root.stat().st_dev:
            raise ValueError('Shared admission must remain on source NFS')
        for key in ['contract','config','receipt','proof','candidate','child_proof','child_stats','review','bridge_code']:
            setattr(obj,key,data[key])
        obj.child=canonical(data['child']);obj.stats=data['source_stats']
        obj._stable_hashes=data['source_sha256'];obj._operational_code=data['operational_code']
        revision=json.loads(obj.revision_path.read_text())
        cert=obj.root/'checkpoint_certificates'/(obj.child.name+'.json')
        required={str(canonical(p)) for p in [obj.root/'contract.json',obj.root/'main.json',obj.revision_path,
            obj.root/'parent_handoff.json',obj.root/'parent_certificate.json',cert,
            *[resolve(p) for p in {**obj.contract['code'],**obj.contract['evidence'],**revision['code']}],
            *[canonical(v['backup']) for v in revision['local_proof_backups'].values()]]}
        if (set(obj._stable_hashes)!=required or set(obj.bridge_code)!=set(BRIDGE_FILES) or
                set(obj._operational_code)!={'train/independent_operational_admission.py',*BRIDGE_FILES} or
                any(obj.bridge_code[p]!=obj._operational_code[p] for p in BRIDGE_FILES)):
            raise ValueError('Shared admission immutable binding coverage differs')
        for actual,key in [(obj.root/'contract.json','contract'),(obj.root/'main.json','config'),
                (obj.root/'parent_handoff.json','receipt'),(obj.root/'parent_certificate.json','proof'),(cert,'child_proof')]:
            if json.loads(actual.read_text())!=getattr(obj,key):raise ValueError('Shared admission source content differs')
        if (obj.candidate!=bound_candidate(obj.receipt['parent_metadata'],obj.receipt['original_contract'],obj.receipt['execution_binding']) or
                obj.candidate['monitored_evaluation']['code_sha256']!=obj.bridge_code[WORKER] or
                obj.child_proof.get('status')!='passed_main_checkpoint_storage' or
                obj.child_proof['training_contract_id']!=obj.candidate['id'] or
                canonical(resolve(obj.child_proof['checkpoint']))!=obj.child or
                obj.child!=obj.root/'training'/f"ckpt_{obj.child_proof['step']:07d}" or
                obj.child.stat().st_dev!=obj.root.stat().st_dev or
                obj.review['checkpoint']['file_stats']!=obj.child_stats or
                obj.review['checkpoint']['full_payload_sha_refreshed'] is not True or
                obj.review['training_contract_id']!=obj.candidate['id'] or
                obj.review['checkpoint']['path']!=str(obj.child) or
                obj.review['checkpoint']['bytes']!=obj.child_proof['bytes'] or
                obj.review['checkpoint']['certificate_sha256']!=sha256_file(cert) or
                revision['child']['checkpoint']!=str(obj.child) or
                revision['child']['step']!=obj.child_proof['step'] or
                revision['child']['certificate_sha256']!=sha256_file(cert)):
            raise ValueError('Shared admission original identity/own checkpoint differs')
        candidates=complete_checkpoints(obj.root/'training')
        if not candidates or candidates[-1]!=obj.child:
            raise ValueError('Shared admission no longer selects latest completed NFS checkpoint')
        obj.check();inventory(obj.child,obj.child_proof)
        verify_restart_metadata(json.loads((obj.child/'meta.json').read_text()),obj.config,obj.candidate,obj.child_proof['step'])
        if sha256_file(path)!=expected_sha256:raise ValueError('Shared receipt changed during rank admission')
        return obj

    def check(self):
        # Initial construction uses the existing fresh content review. The
        # native worker rewrites identical training_contract.json at startup;
        # bytes, not its replaced inode, are the enduring execution binding.
        if self._stable_hashes is None:return super().check()
        if json.loads((self.root/'training/training_contract.json').read_text())!=self.candidate:
            raise ValueError('Operational training contract identity/content changed')
        for p,h in self._stable_hashes.items():
            if sha256_file(canonical(p))!=h:raise ValueError('Operational execution input changed: '+p)
        for p,h in self._operational_code.items():
            if sha256_file(resolve(p))!=h:raise ValueError('Operational admission implementation changed')
        if self.adapter is None or not self.adapter.consumed:
            from train.final_resume_probe import file_stats
            if file_stats(self.child)!=self.child_stats:raise ValueError('Unconsumed own checkpoint changed')

    def resume(self,receipt,candidate,proof,config,contract):
        self.check()
        if self.adapter is not None:raise ValueError('One own-resume adapter per operational attempt')
        if (receipt!=self.receipt or candidate!=self.candidate or proof!=self.proof or
                config!=self.config or contract!=self.contract):
            raise ValueError('Operational worker changed admission identity')
        self.adapter=ConsumedChildResume(rank=self.rank,original_contract=receipt['original_contract'],
            parent_checkpoint=resolve(receipt['parent_checkpoint']),destination=resolve(config['out_dir']),
            parent_metadata=receipt['parent_metadata'],child_contract=candidate,parent_proof=proof,
            verified_parent_stats=receipt['parent_stats'],execution_binding=contract['extension_execution'],
            child_checkpoint=self.child,child_proof=self.child_proof,verified_child_stats=self.child_stats)
        return self.adapter

    def consumption_record(self):
        self.check()
        if self.adapter is None or not self.adapter.consumed:
            raise ValueError('Own source has not been fully consumed')
        fields=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()
        return dict(status='rank_own_nfs_state_consumed',source_run=str(self.root),rank=self.rank,
            checkpoint=str(self.child),step=self.child_proof['step'],training_contract_id=self.candidate['id'],
            checkpoint_certificate_id=identity(self.child_proof),pid=os.getpid(),start_ticks=int(fields[19]),
            model_optimizer_load_completed=True,cpu_cuda_rng_applied=True,
            source_may_rotate_after_all8rank_consumption=True,actual8gpu_acceptance=False)

    @contextmanager
    def operational_hooks(self,on_consumed):
        """Explicit caller must bind admission before invoking worker.worker.

        The callback persists this rank's consumption receipt. Receipt failure
        is fatal; caller/supervisor must not infer all-rank consumption from it.
        Original numerical worker object, config and training ID are unchanged.
        """
        import torch
        from train import monitored_extended_horizon as worker
        from train.extended_handoff import load_handoff,parent_resume
        self.check()
        if (canonical(worker.__file__)!=canonical(resolve(WORKER)) or worker.load_handoff is not load_handoff or
                worker.parent_resume is not parent_resume or getattr(torch.load,'independent_rng_hook',False)):
            raise ValueError('Conflicting operational admission hooks')
        original_load=torch.load;original_cuda=torch.cuda.set_rng_state;original_cpu=torch.set_rng_state
        def load(path,*args,**kwargs):
            a=self.adapter
            if isinstance(path,(str,os.PathLike)) and Path(path).name.startswith('rng_rank'):
                if a is None:raise ValueError('RNG load before own admission')
                return a.read_rng(original_load,path,*args,**kwargs)
            return original_load(path,*args,**kwargs)
        load.independent_rng_hook=True
        def cpu_rng(state):
            a=self.adapter
            if a is None or not a.rng_read or a.consumed:return original_cpu(state)
            if state is not a.rng_cpu or a.cpu_rng_applied:
                raise ValueError('CPU RNG application differs from admitted state')
            result=original_cpu(state);a.cpu_rng_applied=True
            return result
        def cuda_rng(state,device='cuda'):
            a=self.adapter
            if a is None or not a.rng_read or a.consumed:
                return original_cuda(state,device)
            dev=torch.device(device)
            if dev.type!='cuda' or dev.index!=self.rank or state is not a.rng_cuda or not a.cpu_rng_applied:
                raise ValueError('RNG application device differs from admitted rank')
            result=original_cuda(state,device)
            a.consumed_after_rng();on_consumed(self.consumption_record())
            return result
        worker.load_handoff=self.handoff;worker.parent_resume=self.resume
        torch.load=load;torch.cuda.set_rng_state=cuda_rng;torch.set_rng_state=cpu_rng
        try:
            yield worker
            self.check()
            if self.adapter is None or not self.adapter.consumed:
                raise ValueError('Numerical worker returned without full own-state consumption')
        finally:
            worker.load_handoff=load_handoff;worker.parent_resume=parent_resume
            torch.load=original_load;torch.cuda.set_rng_state=original_cuda;torch.set_rng_state=original_cpu
