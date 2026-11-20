"""Synthetic8rank ledger accounting, including exact57000 final coverage."""
from copy import deepcopy
import unittest

from common.data_contracts import identity
from scripts.audit_extended_horizon import aggregate_restored_lineage
from train.extended_horizon_transition import build_extension_candidate


def seal(d):
    d=deepcopy(d); d.pop('id',None); d['id']=identity(d); return d


def records(contract, start, end, attempt, *, success=False):
    schedule=contract['monitored_evaluation']['generation_steps']
    return [(attempt,dict(rank=r,success=success,contract_id=contract['id'],
        optimizer_initializations=1,
        steps=[dict(step=s,source_sequences={'ko':64}) for s in range(start,end+1)],
        generations=[dict(step=s,sha256='a'*64) for s in schedule if start<=s<=end]))
        for r in range(8)]


class ExtendedLedgerTests(unittest.TestCase):
    def setUp(self):
        cfg=dict(max_steps=57000,lr=.0003,warmup=1000,decay_frac=.15,
                 min_lr_ratio=.1,global_tokens=2097152,seq_len=4096,sampler_version=2)
        o=seal(dict(config=cfg,world_size=8,source_order=['ko'],files={},mix={},
            holdout={},execution={},code={},monitored_evaluation=dict(
                probe_sha256='p',prompts_sha256='g',generation_steps=[250,10000,35000,57000])))
        p=deepcopy(o);p['config']['max_steps']=35000
        p['monitored_evaluation']['generation_steps']=[250,10000,35000]
        p['horizon_transition']=dict(parent_contract_id=o['id'],parent_step=8000)
        p=seal(p)
        meta=dict(training_contract=p,config=p['config'],step=10000,world_size=8,
                  sampler_version=2,tokens_per_step=2097152)
        c,_=build_extension_candidate(meta,o)
        self.args=dict(original=o,current=p,restored=c,boundary=10000,
            through_step=10002,original_records=records(o,1,8000,'original'),
            current_records=records(p,8001,10000,'current'),
            restored_records=records(c,10001,10002,'restored'))

    def test_three_contracts_and_no_mutation_or_execution_authorization(self):
        before=deepcopy(self.args)
        result=aggregate_restored_lineage(**self.args)
        self.assertEqual(self.args,before)
        self.assertEqual(result['committed_tokens'],10002*2097152)
        self.assertEqual(result['committed_step_rank_pairs'],80016)
        self.assertEqual([s['contract_id'] for s in result['segments']],
                         [self.args[k]['id'] for k in ['original','current','restored']])
        for key in ['runtime_authorized','lineage_sealed','checkpoint_payload_verified',
                    'distributed_resume_verified']:self.assertFalse(result[key])

    def test_full57000_including_final_all_rank_receipts(self):
        self.args['through_step']=57000
        self.args['restored_records']=records(self.args['restored'],10001,57000,'restored',success=True)
        result=aggregate_restored_lineage(**self.args)
        self.assertEqual(result['committed_tokens'],119537664000)
        self.assertEqual(result['committed_step_rank_pairs'],456000)
        self.assertEqual(set(result['generation_sha256_by_step']),{'250','10000','35000','57000'})
        self.args['restored_records'][-1][1]['success']=False
        with self.assertRaisesRegex(ValueError,'successful receipts'):
            aggregate_restored_lineage(**self.args)

    def test_replay_counted_once_and_parent_tail_excluded(self):
        self.args['original_records']=records(self.args['original'],1,8001,'original')
        self.args['current_records']=records(self.args['current'],8001,10001,'current')
        self.args['restored_records']+=records(self.args['restored'],10002,10002,'retry')
        result=aggregate_restored_lineage(**self.args)
        self.assertEqual(result['replayed_tokens_through_prefix'],2097152)
        self.assertEqual(result['committed_tokens'],10002*2097152)
        for segment in result['segments'][:2]:
            self.assertEqual(segment['persisted_attempt_tokens_beyond_prefix'],2097152)

    def test_gap_overlap_wrong_identity_and_missing_rank_rejected(self):
        for mode in ['gap','overlap','identity','rank','source']:
            with self.subTest(mode=mode):
                args=deepcopy(self.args);rows=args['restored_records']
                if mode=='gap':rows[0][1]['steps'].pop(0)
                if mode=='overlap':rows[0][1]['steps'][0]['step']=10000
                if mode=='identity':rows[0][1]['contract_id']=args['current']['id']
                if mode=='rank':rows.pop()
                if mode=='source':rows[0][1]['steps'][0]['source_sequences']={'unknown':64}
                with self.assertRaises(ValueError):aggregate_restored_lineage(**args)

    def test_boundary_only_is_not_child_progress(self):
        self.args.update(through_step=10000,restored_records=[])
        result=aggregate_restored_lineage(**self.args)
        self.assertEqual(len(result['segments']),2)
        self.assertEqual(result['successful_target_ranks'],[])
        self.args['restored_records']=records(self.args['restored'],10001,10002,'restored')
        with self.assertRaises(ValueError):aggregate_restored_lineage(**self.args)


if __name__=='__main__':unittest.main()
