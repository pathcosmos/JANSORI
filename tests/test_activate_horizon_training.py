"""Activation gates with synthetic diagnostic receipts; never production launch."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common.data_contracts import resolve,sha256_file,identity
from scripts.activate_horizon_training import activation


class ActivationTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix='activation-fixture-',dir=resolve('runs'));self.addCleanup(t.cleanup)
        self.base=Path(t.name);self.root=self.base/'new';self.root.mkdir();self.source=self.base/'old';self.source.mkdir()
        self.draftdir=self.base/'draft';self.draftdir.mkdir();(self.draftdir/'probe').mkdir()
        self.probe=self.root/'horizon_probe';self.probe.mkdir()
        def write(p,v):p.write_text(json.dumps(v)+'\n')
        self.write=write
        write(self.source/'contract.json',dict(tiered_retention={'fixture':'old'}))
        config=dict(max_steps=35000,out_dir=str(self.root/'training'));write(self.draftdir/'main.json',config)
        for name in ['expected_tokens.json','probe/probe.json','probe/.done']:(self.draftdir/name).write_text('{}')
        self.capture=self.base/'capture.json';write(self.capture,{'processes':{'fixture':'captured'}})
        self.receipt=dict(stopped_parent_processes={'fixture':'captured'},execution_binding={},parent_metadata={'step':8000})
        write(self.root/'parent_handoff.json',self.receipt)
        self.auth=self.base/'authorization.json'
        self.approval=dict(authorized=True,new_run_training_authorized=True,retention_transfer_authorized=True,
            target_steps=35000,source_run=str(self.source),new_run=str(self.root),user_instruction='전환해')
        write(self.auth,self.approval)
        self.draft=self.draftdir/'contract.draft.json'
        write(self.draft,dict(purpose='horizon_preparation_not_executable',intended_run=str(self.root),source_run=str(self.source),
            requested_target_steps=35000,code={},evidence={},source_contract_sha256=sha256_file(self.source/'contract.json'),
            configurations={'main.json':sha256_file(self.draftdir/'main.json')},tiered_retention={'local_root':str(self.base/'local')}))
        self.plan={'synthetic_only':True}
        self.report=dict(status='passed_actual_8rank_horizon_resume',plan_id=identity(self.plan),
            main_committed_tokens_added=0,main_target_modified=False,evidence={})
        write(self.probe/'report.json',self.report);self.marker()
        for name,kw in [('scripts.activate_horizon_training.require_parent_absent',{}),
            ('scripts.run_horizon_probe.load_plan',{'return_value':(self.plan,self.probe)}),
            ('scripts.run_horizon_probe.verify_certificates',{}),
            ('scripts.activate_horizon_training.enforce_runtime_space',{}),
            ('scripts.activate_horizon_training.validate_configuration',{}),
            ('scripts.activate_horizon_training.require_authorization',{}),
            ('scripts.activate_horizon_training.load_handoff',{'return_value':(self.receipt,{}, {})}),
            ('scripts.activate_horizon_training.validate_parent_budget',{}),
            ('scripts.activate_horizon_training.HorizonRetention',{})]:
            m=patch(name,**kw);m.start();self.addCleanup(m.stop)

    def marker(self):self.write(self.probe/'.done',dict(plan_id=identity(self.plan),report_sha256=sha256_file(self.probe/'report.json')))
    def activate(self):return activation(self.root,self.draft,self.capture,self.auth)

    def test_exact_materialization_and_retry_do_not_launch(self):
        result=self.activate();self.assertEqual(result,self.activate())
        c=json.loads((self.root/'contract.json').read_text())
        self.assertEqual(c['target_steps'],35000)
        self.assertEqual(c['legacy_retention']['parent_step'],8000)
        self.assertEqual(c['legacy_retention']['source_run'],str(self.source))
        self.assertFalse((self.root/'pid').exists())
        self.assertFalse(result['goal_complete'])

    def test_missing_permission_rejects_before_materializing(self):
        self.approval['new_run_training_authorized']=False;self.write(self.auth,self.approval)
        with self.assertRaisesRegex(ValueError,'authorization'):self.activate()
        self.assertFalse((self.root/'authorization.json').exists())

    def test_missing_marker_or_wrong_hash_does_not_materialize(self):
        self.write(self.probe/'.done',dict(plan_id=identity(self.plan),report_sha256='0'*64))
        with self.assertRaisesRegex(ValueError,'certificate'):self.activate()
        self.assertFalse((self.root/'contract.json').exists())

    def test_diagnostic_tokens_cannot_be_added_to_main(self):
        self.report['main_committed_tokens_added']=4194304;self.write(self.probe/'report.json',self.report);self.marker()
        with self.assertRaisesRegex(ValueError,'certificate'):self.activate()

    def test_changed_evidence_refused(self):
        evidence=self.base/'evidence';evidence.write_text('a')
        self.report['evidence']={str(evidence):sha256_file(evidence)}
        self.write(self.probe/'report.json',self.report);self.marker();evidence.write_text('b')
        with self.assertRaisesRegex(ValueError,'evidence changed'):self.activate()

    def test_foreign_existing_contract_is_preserved(self):
        (self.root/'contract.json').write_text('preserve')
        with self.assertRaisesRegex(ValueError,'Different handoff residue'):self.activate()
        self.assertEqual((self.root/'contract.json').read_text(),'preserve')


if __name__=='__main__':unittest.main()
