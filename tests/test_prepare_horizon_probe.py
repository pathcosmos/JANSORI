"""Small real NFS copies/seals/spec admission, synthetic original audit/payload.

No CUDA or production checkpoint writes. Capacity admission is mocked because
fixture reservations are tiny; its actual formulas have separate coverage.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common.data_contracts import resolve, identity, sha256_file, artifact_path
from scripts.audit_main_training import aggregate
from scripts.protect_horizon_parent import protect_parent, GIB
from scripts.seal_horizon_handoff import prepare_lineage, seal_handoff
from scripts.prepare_horizon_probe import prepare, resume_spec
from scripts.supervise_main_training import require_authorization
from train.horizon_resume_probe import check_spec
from train.horizon_worker_contract import BRIDGE_CODE
from train import train as trainer
from train.runtime import RESUME_KEYS
from train.final_resume_probe import file_stats


class ProbePreparationTests(unittest.TestCase):
    def setUp(self):
        dirs=[]
        for prefix in ('probe-main-', 'probe-source-', 'test_probe_continuous_', 'test_probe_split_'):
            t=tempfile.TemporaryDirectory(prefix=prefix, dir=resolve('runs'))
            self.addCleanup(t.cleanup);dirs.append(Path(t.name))
        self.root,self.source,self.continuous,self.split=dirs
        self.continuous.rmdir();self.split.rmdir()
        self.original=dict(trainer.DEFAULT, model='3b',micro_bsz=4,max_steps=57000,
            lr=.0003,warmup=1000,compile=False,keep_ckpts=57001,out_dir=str(self.source/'training'))
        schedule=[250,2500,5000,10000,35000,57000]
        parent=dict(config={k:self.original[k] for k in RESUME_KEYS},world_size=8,
            files={'synthetic':'fixture'},mix={},source_order=['ko'],holdout=[],code={},
            execution={'deterministic_algorithms':True},
            monitored_evaluation=dict(code_sha256='fixture-parent',generation_steps=schedule))
        parent['id']=identity(parent)
        self.meta=dict(step=500,world_size=8,tokens_per_step=2097152,config=self.original,
                       training_contract=parent,sampler_version=2)
        cp=self.source/'training/ckpt_0000500';cp.mkdir(parents=True)
        (cp/'meta.json').write_text(json.dumps(self.meta))
        for name in ['.complete','.metadata','__0_0.distcp',*[f'rng_rank{r}.pt' for r in range(8)]]:
            (cp/name).write_text('synthetic-payload-'+name)
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(cp.iterdir())]
        self.proof=dict(step=500,training_contract_id=parent['id'],files=files,
                        bytes=sum(r['bytes'] for r in files),checkpoint=str(cp))
        (self.source/'checkpoint_certificates').mkdir()
        (self.source/'checkpoint_certificates/ckpt_0000500.json').write_text(json.dumps(self.proof))
        (self.source/'main.json').write_text(json.dumps(self.original))
        (self.source/'contract.json').write_text(json.dumps(dict(generation_steps=schedule)))
        (self.source/'expected_tokens.json').write_text('{}')
        (self.source/'probe').mkdir()
        (self.source/'probe/probe.json').write_text(json.dumps(dict(status='passed_probe_selection',
            production_input=True,evidence={},code={})))
        (self.source/'probe/.done').write_text(json.dumps(dict(probe_sha256=sha256_file(self.source/'probe/probe.json'))))
        (self.source/'training/log.jsonl').write_text(json.dumps(dict(step=500,tok_s=1))+'\n')
        (self.source/'ledgers').mkdir();attempts=[]
        for rank in range(8):
            row=dict(rank=rank,success=False,contract_id=parent['id'],optimizer_initializations=0,
                steps=[dict(step=s,source_sequences={'ko':64}) for s in range(1,501)],
                generations=[dict(step=250,sha256='a'*64)])
            attempts.append(('fixture',row))
            (self.source/'ledgers'/f'fixture-rank{rank}.json').write_text(json.dumps(row))
        counts=aggregate(attempts,world=8,through_step=500,target_steps=57000,seq_len=4096,
                         global_tokens=2097152,sources=['ko'],generation_steps=schedule,contract_id=parent['id'])
        self.audit=dict(counts=counts,trace={'synthetic':True},code={},through_step=500,source_run=str(self.source),
            evidence={artifact_path(p):sha256_file(p) for p in
                [self.source/'training/log.jsonl',*sorted((self.source/'ledgers').glob('*.json'))]})
        m=patch('scripts.seal_horizon_handoff.audit_run',return_value=self.audit)
        m.start();self.addCleanup(m.stop)
        protect_parent(cp,self.root,self.proof,owner_id='a'*32,required_free_after_bytes=200*GIB)
        template=self.root/'lineage_template';prepare_lineage(self.source,500,template)
        self.capture=dict(status='observed_live_not_stopped',run=str(self.source),processes={
            k:dict(pid=2147483500+i,start_ticks=1) for i,k in
            enumerate(['supervisor','worker',*[f'rank{r}' for r in range(8)]])})
        self.capture_path=self.source/'capture.json';self.capture_path.write_text(json.dumps(self.capture))
        seal_handoff(self.source,self.root,self.capture_path,template,owner_id='a'*32)
        code=BRIDGE_CODE|{'train/monitored.py','train/final_resume_probe.py',
            'scripts/audit_main_training.py','scripts/tiered_checkpoint_retention.py'}
        self.draft=dict(purpose='horizon_preparation_not_executable',requested_target_steps=35000,
            intended_run=str(self.root),source_run=str(self.source),
            source_contract_sha256=sha256_file(self.source/'contract.json'),
            code={p:sha256_file(resolve(p)) for p in code},evidence={},framework={'fixture':True},
            prompts='unused-fixture',tokenizer='unused-fixture',gpu_identity=[],generation_steps=schedule,
            space_reservations=dict(nfs={str(self.continuous):GIB,str(self.split):GIB}))
        self.draft_path=self.source/'draft.json';self.write_draft()
        m=patch('scripts.prepare_horizon_probe.enforce_runtime_space',return_value={'nfs_remaining_bytes':2*GIB})
        self.space=m.start();self.addCleanup(m.stop)

    def write_draft(self):self.draft_path.write_text(json.dumps(self.draft))
    def prepare(self):return prepare(self.root,self.draft_path,self.capture_path)

    def test_real_copy_seal_admission_and_same_transaction_retry(self):
        result=self.prepare();self.assertEqual(result,self.prepare())
        self.assertFalse(result['actual8gpu_acceptance'])
        plan=json.loads(Path(result['plan']).read_text())
        for mode in ('baseline','continuous','first'):
            spec=json.loads((self.root/'horizon_probe'/f'{mode}.json').read_text())
            _,root,_,config,receipt,candidate,proof,selected,stats=check_spec(spec,mode)
            self.assertEqual(config['stop_after'],501 if mode=='first' else 502)
            self.assertEqual(candidate['id'],plan['child_contract_id'])
            self.assertEqual(selected.readlink() if selected.is_symlink() else selected.name,'ckpt_0000500')
            self.assertEqual(stats,file_stats(selected))
            with self.assertRaises(ValueError):require_authorization(json.loads((root/'contract.json').read_text()))
        self.assertFalse((self.root/'horizon_probe/resumed.json').exists())
        self.assertGreaterEqual(self.space.call_count,6)

    def test_live_parent_refused_before_any_diagnostic_write(self):
        stat=Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')',1)[1].split()
        self.capture['processes']['worker']=dict(pid=os.getpid(),start_ticks=int(stat[19]))
        self.capture_path.write_text(json.dumps(self.capture))
        with self.assertRaisesRegex(ValueError,'still present'):self.prepare()
        self.assertFalse(self.continuous.exists())

    def test_unknown_root_or_changed_draft_preserves_residue(self):
        self.continuous.mkdir();(self.continuous/'keep').write_text('unrelated')
        with self.assertRaisesRegex(ValueError,'Unowned'):self.prepare()
        self.assertEqual((self.continuous/'keep').read_text(),'unrelated')
        self.assertFalse(self.split.exists())

    def test_owned_partial_copy_retries_without_changing_parent(self):
        from scripts.prepare_horizon_probe import seal_handoff as actual_seal
        calls=[]
        def once(*args,**kwargs):
            calls.append(1)
            if len(calls)==1:raise InterruptedError('fixture after first copy')
            return actual_seal(*args,**kwargs)
        with patch('scripts.prepare_horizon_probe.seal_handoff',side_effect=once):
            with self.assertRaises(InterruptedError):self.prepare()
        copied=self.continuous/'parent_checkpoint/ckpt_0000500'
        before=file_stats(copied)
        self.prepare();self.assertEqual(before,file_stats(copied))

    def test_changed_code_or_missing_probe_completion_rejected(self):
        self.draft['code']['train/monitored.py']='0'*64;self.write_draft()
        with self.assertRaisesRegex(ValueError,'Source evidence changed'):self.prepare()
        self.assertFalse(self.continuous.exists())
        self.draft['code']['train/monitored.py']=sha256_file(resolve('train/monitored.py'));self.write_draft()
        (self.source/'probe/.done').unlink()
        with self.assertRaises(FileNotFoundError):self.prepare()
        self.assertFalse((self.root/'horizon_probe/preparation.json').exists())

    def test_space_refusal_before_copy_and_changed_owner_after_partial(self):
        self.space.side_effect=RuntimeError('fixture unavailable capacity')
        with self.assertRaisesRegex(RuntimeError,'capacity'):self.prepare()
        self.assertFalse(self.continuous.exists())
        self.space.side_effect=None;self.prepare()
        self.draft['framework']={'changed':True};self.write_draft()
        with self.assertRaisesRegex(ValueError,'different diagnostic residue'):self.prepare()

    def test_resume_spec_requires_fresh_full_cuda_certificate(self):
        result=self.prepare();path=Path(result['plan'])
        with self.assertRaises(FileNotFoundError):resume_spec(path)
        cert=self.split/'certificates/first.json';cert.parent.mkdir()
        cert.write_text(json.dumps(dict(status='certified_horizon_probe_checkpoint',cuda_rng_required=False)))
        with self.assertRaisesRegex(ValueError,'CUDA checkpoint certification'):resume_spec(path)


if __name__=='__main__':unittest.main()
