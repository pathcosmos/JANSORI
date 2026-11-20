"""CPU integration with explicitly supplied real prefix metadata.

Set DENSE_BUDGET_LINEAGE_FIXTURE to a prepared prefix directory. Checkpoint
payload and stopped-process/8GPU-report fixtures are synthetic. No CUDA or
production processes are started, stopped, copied or removed by these tests.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from common.data_contracts import resolve,identity,sha256_file
from scripts.budget_horizon_lineage import verify_lineage
from train.budget_horizon_handoff import load_handoff,validate_configuration
from train.budget_horizon_contract import BRIDGE_CODE,bound_candidate
from train.final_resume_probe import file_stats


@unittest.skipUnless(os.environ.get('DENSE_BUDGET_LINEAGE_FIXTURE'),
                     'Requires explicit immutable prefix metadata fixture')
class BudgetHandoffIntegrationTests(unittest.TestCase):
    def setUp(self):
        fixture=Path(os.environ['DENSE_BUDGET_LINEAGE_FIXTURE']).resolve()
        temp=tempfile.TemporaryDirectory(prefix='budget-admission-fixture-',dir=resolve('runs'))
        self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        shutil.copytree(fixture,self.root/'lineage')
        self.meta=json.loads((fixture/'parent_metadata.json').read_text())
        self.probe=json.loads(resolve('runs/3b_full_57k_v2/probe/probe.json').read_text())
        original_outer=json.loads(resolve('runs/3b_full_57k_v2/contract.json').read_text())
        self.prompts_path=resolve(original_outer['prompts'])
        self.prompts=[json.loads(x) for x in self.prompts_path.read_text().splitlines() if x.strip()]
        (self.root/'probe').mkdir()
        shutil.copyfile(resolve('runs/3b_full_57k_v2/probe/probe.json'),self.root/'probe/probe.json')
        self.source=self.root.parent/(self.root.name+'-source')
        self.ancestor=self.root.parent/(self.root.name+'-ancestor')
        manifest=json.loads((self.root/'lineage/manifest.json').read_text());manifest['source_run']=str(self.source)
        self.write(self.root/'lineage/manifest.json',manifest)
        self.parent=self.root/'parent_checkpoint'/f"ckpt_{self.meta['step']:07d}";self.parent.mkdir(parents=True)
        self.write(self.parent/'meta.json',self.meta)
        for name in ['.complete','.metadata','__0_0.distcp',*[f'rng_rank{r}.pt' for r in range(8)]]:
            (self.parent/name).write_bytes(b'synthetic payload: not a loadable3B checkpoint')
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(self.parent.iterdir())]
        self.proof=dict(step=self.meta['step'],training_contract_id=self.meta['training_contract']['id'],
                        files=files,bytes=sum(r['bytes'] for r in files))
        self.write(self.root/'parent_certificate.json',self.proof)
        self.binding=dict(parent_contract_id=self.meta['training_contract']['id'],
            parent_step=self.meta['step'],parent_metadata_id=identity(self.meta),
            parent_certificate_sha256=sha256_file(self.root/'parent_certificate.json'),
            parent_certificate_id=identity(self.proof),parent_ledger_manifest_sha256=sha256_file(self.root/'lineage/manifest.json'),
            code={n:sha256_file(resolve(n)) for n in BRIDGE_CODE})
        roles=['supervisor','worker',*[f'rank{r}' for r in range(8)]]
        def capture(root,start):
            return dict(status='observed_live_not_stopped',run=str(root),
                processes={name:dict(pid=start+i,start_ticks=1) for i,name in enumerate(roles)})
        self.receipt=dict(status='sealed_budget_parent_handoff',execution_binding=self.binding,
            protection_root=str(self.root),parent_metadata=self.meta,
            parent_checkpoint=str(self.parent),parent_stats=file_stats(self.parent),source_run=str(self.source),
            original_run=str(self.ancestor),source_capture=capture(self.source,1000000000),
            original_capture=capture(self.ancestor,1000000100),
            stopped_waiting_controllers={name:dict(pid=1000000200+i,start_ticks=1) for i,name in enumerate(
                ['observer','followup'])})
        self.write(self.root/'parent_handoff.json',self.receipt)
        self.candidate=bound_candidate(self.meta,self.binding)
        self.config=dict(self.meta['config'],max_steps=50000,keep_ckpts=50001,out_dir=str(self.root/'training'))
        self.config.pop('stop_after',None)
        self.report=dict(status='passed_actual_8rank_budget_resume',world_size=8,parent_step=self.meta['step'],
            parent_contract_id=self.meta['training_contract']['id'],child_contract_id=self.candidate['id'],
            model_optimizer_rng_exact=True,next_update_exact=True,split_resume_exact=True,
            evidence={str(self.root/'synthetic_probe.txt'):'pending'})
        (self.root/'synthetic_probe.txt').write_text('Synthetic protocol fixture; not actual8GPU evidence')
        self.report['evidence'][str(self.root/'synthetic_probe.txt')]=sha256_file(self.root/'synthetic_probe.txt')
        self.write(self.root/'validation.json',self.report)
        self.contract=dict(main_run=str(self.root),purpose='main_training',main_training_authorized=True,
            budget_execution=self.binding,code=self.binding['code'],evidence={},prompts=str(self.prompts_path),
            generation_steps=self.candidate['monitored_evaluation']['generation_steps'],
            budget_validation_report=str(self.root/'validation.json'))
        local=tempfile.TemporaryDirectory(prefix='budget-admission-local-');self.addCleanup(local.cleanup)
        scratch=Path(local.name);gib=2**30
        self.contract.update(expected_checkpoint_bytes=2**20,shared_storage=dict(
            reserve_bytes=200*gib,temporary_budget_bytes=1024*gib,
            nfs_pools=dict(history=dict(paths=[str(p) for p in [self.root,self.source,self.ancestor]],budget_bytes=gib)),
            local_history_paths=[str(scratch/f'history{i}') for i in range(3)],
            local_temp_paths=[str(scratch/'temporary')],local_history_budget_bytes=gib))
        self.middle=self.root.parent/(self.root.name+'-middle')
        self.contract['retention_branches']=[dict(run=str(p),stopped_capture=capture(p,1000001000+i*100))
            for i,p in enumerate([self.ancestor,self.middle,self.source,self.root])]
        self.contract['retention_branches'][2]['stopped_capture']=self.receipt['source_capture']
        self.contract['shared_storage']['nfs_pools']['history']['paths'].append(str(self.middle))
        # Only capacity is simulated: fixtures must not reserve a second TiB
        # alongside the active production temporary pool. Files/SHA are real.
        from unittest.mock import patch
        from types import SimpleNamespace
        real_disk_usage=shutil.disk_usage
        mock=patch('scripts.extended_horizon_storage.shutil.disk_usage',
            side_effect=lambda p: SimpleNamespace(free=2048*gib) if Path(p).is_relative_to(scratch) else real_disk_usage(p))
        mock.start();self.addCleanup(mock.stop)
        self.rebind()

    @staticmethod
    def write(path,value):path.write_text(json.dumps(value)+'\n')

    def rebind(self):
        self.contract['evidence']={str(self.root/name):sha256_file(self.root/name)
                                   for name in ['parent_handoff.json','validation.json']}

    def test_valid_protocol_fixture_recomputes_actual_prefix(self):
        validate_configuration(self.config,self.contract)
        receipt,child,proof=load_handoff(self.root,self.contract,self.config)
        self.assertEqual(child,self.candidate);self.assertEqual(proof,self.proof)
        self.assertEqual(receipt['parent_metadata']['step'],15500)

    def test_missing_or_false_actual_gpu_evidence_rejected(self):
        for key in ['model_optimizer_rng_exact','next_update_exact','split_resume_exact']:
            bad=deepcopy(self.report);bad[key]=False;self.write(self.root/'validation.json',bad);self.rebind()
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'validation incomplete'):
                load_handoff(self.root,self.contract,self.config)
        self.write(self.root/'validation.json',self.report);self.rebind()
        (self.root/'synthetic_probe.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError,'evidence changed'):load_handoff(self.root,self.contract,self.config)

    def test_live_controller_unsealed_receipt_and_protected_mutation_rejected(self):
        r=deepcopy(self.receipt);r['status']='prepared_only';self.write(self.root/'parent_handoff.json',r);self.rebind()
        with self.assertRaisesRegex(ValueError,'Unsealed'):load_handoff(self.root,self.contract,self.config)
        r=deepcopy(self.receipt);ticks=int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19])
        r['stopped_waiting_controllers']['observer']=dict(pid=os.getpid(),start_ticks=ticks)
        self.write(self.root/'parent_handoff.json',r);self.rebind()
        with self.assertRaisesRegex(ValueError,'controller still present'):load_handoff(self.root,self.contract,self.config)
        self.write(self.root/'parent_handoff.json',self.receipt);self.rebind()
        (self.parent/'__0_0.distcp').write_bytes(b'tampered')
        with self.assertRaises(ValueError):load_handoff(self.root,self.contract,self.config)

    def test_rehashed_lineage_gap_and_unexpected_residue_rejected(self):
        lineage=self.root/'lineage';path=lineage/'restored_rank7.json'
        row=json.loads(path.read_text());row[0]['record']['steps'].pop(0);self.write(path,row)
        m=json.loads((lineage/'manifest.json').read_text());m['files'][path.name]=sha256_file(path)
        self.write(lineage/'manifest.json',m)
        with self.assertRaises(ValueError):verify_lineage(lineage,self.meta,self.probe,self.prompts)
        (lineage/'foreign').write_text('do not ignore')
        with self.assertRaisesRegex(ValueError,'file scope'):
            verify_lineage(lineage,self.meta,self.probe,self.prompts)

    def test_target_cadence_diagnostic_bounds_and_namespaces_rejected(self):
        for key,value in [('max_steps',35000),('keep_ckpts',3),('ckpt_every',250),('compile',True)]:
            bad=dict(self.config,**{key:value})
            with self.subTest(key=key),self.assertRaises(ValueError):validate_configuration(bad,self.contract)
        diagnostic=dict(self.contract,purpose='budget_horizon_validation')
        with self.assertRaises(ValueError):validate_configuration(dict(self.config,stop_after=15503),diagnostic)
        bad=dict(self.config,out_dir=str(self.root/'other'))
        with self.assertRaisesRegex(ValueError,'namespace'):load_handoff(self.root,self.contract,bad)


if __name__=='__main__':unittest.main()
