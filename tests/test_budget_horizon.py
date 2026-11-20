"""CPU contract/ledger fault tests; no GPU or production mutation."""
import ast
from copy import deepcopy
from pathlib import Path
import unittest

from common.data_contracts import identity
from train.extended_horizon_worker_contract import BRIDGE_CODE as OLD_CODE
from train.extended_horizon_worker_contract import bound_candidate as restored_candidate
from train.budget_horizon_contract import (candidate, validate_candidate, bound_candidate,
    validate_bound_candidate, BRIDGE_CODE, BINDING_KEY)
from scripts.audit_budget_horizon import aggregate_budget_lineage
import test_extended_horizon_audit as previous_fixture

records, seal = previous_fixture.records, previous_fixture.seal


def fixture():
    base = previous_fixture.ExtendedLedgerTests(); base.setUp(); args = base.args
    original, current = args['original'], args['current']
    extension_meta = dict(training_contract=current, config=deepcopy(current['config']),
        step=10000, world_size=8, sampler_version=2, tokens_per_step=2097152)
    binding = dict(original_contract_id=original['id'], parent_contract_id=current['id'],
        parent_step=10000, parent_metadata_id=identity(extension_meta),
        parent_certificate_sha256='a'*64, parent_certificate_id='b'*64,
        parent_ledger_manifest_sha256='c'*64, code={p:'d'*64 for p in OLD_CODE})
    restored = restored_candidate(extension_meta, original, binding)
    parent = dict(training_contract=restored, config=deepcopy(restored['config']),
        step=16000, world_size=8, sampler_version=2, tokens_per_step=2097152)
    child, _ = candidate(parent)
    return dict(original=original, current=current, restored=restored, child=child,
        original_records=args['original_records'], current_records=args['current_records'],
        restored_records=records(restored,10001,16000,'restored'),
        child_records=records(child,16001,16002,'child'), extension_metadata=extension_meta,
        parent_metadata=parent, through_step=16002)


class BudgetContractTests(unittest.TestCase):
    def setUp(self):
        self.args = fixture(); self.meta = self.args['parent_metadata']

    def test_preserves_both_old_bindings_and_actual_lr_prefix(self):
        before = deepcopy(self.meta); child, result = candidate(self.meta)
        self.assertEqual(self.meta, before)
        for field in ['horizon_transition', 'extended_horizon_transition']:
            self.assertEqual(child[field], self.meta['training_contract'][field])
        self.assertEqual(result['total_tokens'], 104857600000)
        self.assertFalse(result['runtime_authorized'])
        tree=ast.parse(Path('train/train.py').read_text())
        fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='lr_at')
        ns={};exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual_lr_at','exec'),ns)
        lr=ns['lr_at']
        self.assertTrue(all(lr(s,child['config'])==lr(s,self.meta['config']) for s in range(42501)))
        self.assertNotEqual(lr(42501,child['config']),lr(42501,self.meta['config']))
        self.assertAlmostEqual(lr(49999,child['config']),.000030036)

    def test_rejects_nonboundary_changed_metadata_and_postdecay(self):
        for key,val in [('step',True),('step',10000),('step',16001),('step',42500),('step',43000),
                        ('world_size',4),('tokens_per_step',1),('sampler_version',1)]:
            with self.subTest(key=key,val=val):
                bad=deepcopy(self.meta);bad[key]=val
                with self.assertRaises(ValueError):candidate(bad)

    def test_rehashed_unreviewed_changes_rejected(self):
        for path,val in [(['config','lr'],.0002),(['files'],'different'),
                         (['horizon_transition','parent_step'],7500),
                         (['extended_horizon_transition','parent_step'],10500),
                         (['monitored_evaluation','generation_steps'],[50000]),(['world_size'],4)]:
            bad=deepcopy(self.args['child']);row=bad
            for key in path[:-1]:row=row[key]
            row[path[-1]]=val
            with self.subTest(path=path),self.assertRaises(ValueError):
                validate_candidate(self.meta,seal(bad))

    def test_bound_identity_rejects_tampering_and_unknown_code(self):
        binding=dict(parent_contract_id=self.meta['training_contract']['id'],parent_step=16000,
            parent_metadata_id=identity(self.meta),parent_certificate_sha256='a'*64,
            parent_certificate_id='b'*64,parent_ledger_manifest_sha256='c'*64,
            code={p:'d'*64 for p in BRIDGE_CODE})
        child=bound_candidate(self.meta,binding)
        validate_bound_candidate(self.meta,child,binding)
        self.assertEqual(child[BINDING_KEY],binding)
        for field in ['horizon_transition','extended_horizon_transition']:
            self.assertEqual(child[field],self.meta['training_contract'][field])
        bad=deepcopy(binding);bad['code']['unreviewed.py']='a'*64
        with self.assertRaises(ValueError):bound_candidate(self.meta,bad)
        bad=deepcopy(binding);bad['parent_step']=16500
        with self.assertRaises(ValueError):bound_candidate(self.meta,bad)
        changed=deepcopy(child);changed[BINDING_KEY]['parent_certificate_id']='e'*64
        with self.assertRaises(ValueError):validate_bound_candidate(self.meta,seal(changed),binding)


class BudgetLedgerTests(unittest.TestCase):
    def test_four_segments_and_replay_are_counted_without_relabeling(self):
        a=fixture();a['restored_records']=records(a['restored'],10001,16001,'restored')
        a['child_records']+=records(a['child'],16002,16002,'retry')
        result=aggregate_budget_lineage(**a)
        self.assertEqual(result['committed_tokens'],16002*2097152)
        self.assertEqual(result['replayed_tokens_through_prefix'],2097152)
        self.assertEqual(result['segments'][2]['persisted_attempt_tokens_beyond_prefix'],2097152)
        self.assertEqual([s['contract_id'] for s in result['segments']],
                         [a[k]['id'] for k in ['original','current','restored','child']])
        self.assertFalse(result['distributed_resume_verified'])

    def test_missing_rank_gap_cross_boundary_wrong_source_and_contract_fail(self):
        for mode in ['rank','gap','overlap','contract','source','parent']:
            a=fixture();row=a['child_records'][0][1]
            if mode=='rank':a['child_records'].pop()
            if mode=='gap':row['steps'].pop()
            if mode=='overlap':row['steps'][0]['step']=16000
            if mode=='contract':row['contract_id']=a['restored']['id']
            if mode=='source':row['steps'][0]['source_sequences']={'unknown':64}
            if mode=='parent':a['parent_metadata']['training_contract']=a['current']
            with self.subTest(mode=mode),self.assertRaises(ValueError):aggregate_budget_lineage(**a)

    def test_full_50000_requires_all_rank_success(self):
        a=fixture();a['through_step']=50000
        a['child_records']=records(a['child'],16001,50000,'child',success=True)
        result=aggregate_budget_lineage(**a)
        self.assertEqual(result['committed_tokens'],104857600000)
        self.assertEqual(result['committed_step_rank_pairs'],400000)
        self.assertEqual(result['successful_target_ranks'],list(range(8)))
        self.assertIn('50000',result['generation_sha256_by_step'])
        self.assertNotIn('57000',result['generation_sha256_by_step'])
        a['child_records'][-1][1]['success']=False
        with self.assertRaises(ValueError):aggregate_budget_lineage(**a)

    def test_boundary_only_does_not_claim_new_progress(self):
        a=fixture();a.update(through_step=16000,child_records=[])
        result=aggregate_budget_lineage(**a)
        self.assertEqual(len(result['segments']),3)
        self.assertEqual(result['successful_target_ranks'],[])


if __name__=='__main__':unittest.main()
