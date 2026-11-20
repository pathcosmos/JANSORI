"""Prepare own-NFS admission hooks around the unchanged monitored worker.

No launcher, signals, pruning or production authorization. A later supervisor
must bind this bridge and independently verify actual8GPU restore before
adoption. The original numerical worker and its training identity stay intact.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path

from common.data_contracts import resolve,sha256_file,identity
from train.parent_checkpoint_resume import canonical
from train.independent_checkpoint_handoff import review_handoff
from train.independent_child_resume import IndependentChildResume
from train.extended_horizon_worker_contract import bound_candidate
from train.final_resume_probe import file_stats,stat_record

WORKER='train/monitored_extended_horizon.py'
BRIDGE_FILES=('train/independent_resume_bridge.py','train/independent_child_resume.py',
              'train/independent_checkpoint_handoff.py',WORKER)


class PreparedIndependentAdmission:
    def __init__(self,root,revision_path):
        self.root=canonical(resolve(root));self.revision_path=canonical(resolve(revision_path))
        self.contract=json.loads((self.root/'contract.json').read_text())
        self.config=json.loads((self.root/'main.json').read_text())
        revision=json.loads(self.revision_path.read_text())
        self.bridge_code={p:sha256_file(resolve(p)) for p in BRIDGE_FILES}
        paths=[self.root/'contract.json',self.root/'main.json',self.revision_path,
               self.root/'parent_handoff.json',self.root/'parent_certificate.json',
               self.root/'training/training_contract.json']
        paths.extend(resolve(p) for p in {**self.contract['code'],**self.contract['evidence'],**revision['code']})
        paths.extend(canonical(entry['backup']) for entry in revision['local_proof_backups'].values())
        cert=self.root/'checkpoint_certificates'/f"ckpt_{revision['child']['step']:07d}.json";paths.append(cert)
        self.stats={str(canonical(p)):stat_record(canonical(p)) for p in paths}
        self.review=review_handoff(self.root,self.contract,self.config,self.revision_path)
        self.receipt=json.loads((self.root/'parent_handoff.json').read_text())
        self.proof=json.loads((self.root/'parent_certificate.json').read_text())
        self.candidate=bound_candidate(self.receipt['parent_metadata'],self.receipt['original_contract'],self.receipt['execution_binding'])
        self.child=canonical(self.review['checkpoint']['path']);self.child_proof=json.loads(cert.read_text())
        self.child_stats=self.review['checkpoint']['file_stats']
        if (self.candidate!=json.loads((self.root/'training/training_contract.json').read_text()) or
                self.candidate['id']!=self.review['training_contract_id'] or
                self.candidate['monitored_evaluation']['code_sha256']!=self.bridge_code[WORKER]):
            raise ValueError('Original numerical worker/training identity differs')
        self.check()

    def check(self):
        for p,stamp in self.stats.items():
            if stat_record(canonical(p))!=stamp:raise ValueError('Independent admission source binding changed: '+p)
        for p,digest in self.bridge_code.items():
            if sha256_file(resolve(p))!=digest:raise ValueError('Independent bridge code changed')
        if file_stats(self.child)!=self.child_stats:raise ValueError('Independent admitted checkpoint changed')

    def handoff(self,root,contract,config):
        self.check()
        if canonical(resolve(root))!=self.root or contract!=self.contract or config!=self.config:
            raise ValueError('Worker requested a different admitted run/configuration')
        return deepcopy(self.receipt),deepcopy(self.candidate),deepcopy(self.proof)

    def resume(self,receipt,candidate,proof,config,contract):
        self.check()
        if (receipt!=self.receipt or candidate!=self.candidate or proof!=self.proof or
                config!=self.config or contract!=self.contract):
            raise ValueError('Worker changed admitted resume inputs')
        return IndependentChildResume(original_contract=receipt['original_contract'],
            parent_checkpoint=resolve(receipt['parent_checkpoint']),destination=resolve(config['out_dir']),
            parent_metadata=receipt['parent_metadata'],child_contract=candidate,parent_proof=proof,
            verified_parent_stats=receipt['parent_stats'],execution_binding=contract['extension_execution'],
            child_checkpoint=self.child,child_proof=self.child_proof,verified_child_stats=self.child_stats)

    def evidence(self):
        self.check()
        return dict(status='prepared_independent_worker_hooks_not_runtime_accepted',source_run=str(self.root),
            step=self.child_proof['step'],checkpoint=str(self.child),training_contract_id=self.candidate['id'],
            original_numerical_worker=WORKER,original_numerical_worker_sha256=self.bridge_code[WORKER],
            runtime_bridge_code=self.bridge_code,revision_sha256=sha256_file(self.revision_path),
            checkpoint_certificate_id=identity(self.child_proof),checkpoint_stats=self.child_stats,
            source_stats=self.stats,actual8gpu_resume=False,production_adoption_authorized=False)

    @contextmanager
    def diagnostic_hooks(self):
        """Explicit process-local diagnostic wiring; never launches the worker.

        The caller supplies its isolated diagnostic execution. There is no
        production CLI or supervisor adoption in this preparation module.
        """
        from train import monitored_extended_horizon as worker
        from train.extended_handoff import load_handoff,parent_resume
        self.check()
        if (canonical(worker.__file__)!=canonical(resolve(WORKER)) or
                worker.load_handoff is not load_handoff or worker.parent_resume is not parent_resume):
            raise ValueError('Numerical worker already has different admission hooks')
        worker.load_handoff=self.handoff;worker.parent_resume=self.resume
        try:
            yield worker
            self.check()
        finally:
            worker.load_handoff=load_handoff;worker.parent_resume=parent_resume
