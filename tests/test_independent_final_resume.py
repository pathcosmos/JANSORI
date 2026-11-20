"""Independent final resume routing; runtime GPU acceptance mocked, file gates real."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common.data_contracts import resolve,identity,sha256_file,artifact_path
from scripts.tiered_checkpoint_retention import atomic_json
from scripts import independent_final_source as source
from scripts.prepare_restored_final_resume import prepare
from scripts.run_restored_final_resume import make_contract,materialize_spec
from train.restored_final_resume_probe import check_spec,verify_final_audit,file_stats
import test_restored_final_resume_admission as legacy_fixture


class FinalIndependentResumeTests(unittest.TestCase):
    write=staticmethod(atomic_json)

    def setUp(self):
        legacy_fixture.FinalHorizonAdmissionTests.setUp(self)
        self.control=self.base/'control';self.control.mkdir()
        self.runtime=self.control/'runtime_contract.json';atomic_json(self.runtime,{'synthetic_not_actual_gpu':True})
        self.reference=dict(control_dir=str(self.control),runtime_contract_sha256=sha256_file(self.runtime))
        self.binding=dict(reference=self.reference,code={p:sha256_file(resolve(p)) for p in source.CODE},inputs={str(self.runtime):sha256_file(self.runtime)})
        self.nc['independent_runtime']=self.reference;self.write(self.native/'contract.json',self.nc)
        self.view=SimpleNamespace(main=self.main,control=self.control,path=self.runtime,digest=sha256_file(self.runtime),
            acceptance={'parent_training_contract_id':self.parent['id'],'synthetic_not_actual_gpu':True},
            observe=lambda:{'completion':self.completion},unchanged=lambda:None)
        self.completion={'certificate_sha256':'not-final-yet'}
        for name,kw in [
            ('train.extended_handoff.load_handoff',dict(side_effect=AssertionError('forbidden original parent payload admission'))),
            ('scripts.independent_final_source.bind_source',dict(return_value=self.binding)),
            ('scripts.independent_final_source.admitted_candidate',dict(return_value=self.parent)),
            ('scripts.independent_final_source.source_view',dict(return_value=self.view))]:
            mock=patch(name,**kw);mock.start();self.addCleanup(mock.stop)

    def plan(self):return prepare(self.main,self.output,native_run=self.native,runtime_control=self.control)

    def spec(self):
        spec=legacy_fixture.FinalHorizonAdmissionTests.spec(self)
        directory=self.control/'final_source_v1';directory.mkdir()
        self.write(directory/'revision.json',{'fixture_only':True});self.write(directory/'review.json',{'fixture_only':True})
        self.write(directory/'prepared.json',dict(revision_sha256=sha256_file(directory/'revision.json'),review_sha256=sha256_file(directory/'review.json'),production_adoption_authorized=False))
        self.completion={'certificate_sha256':spec['parent_proof_sha256']}
        self.audit.update(status='passed_independent_runtime_target_source_audit',parent_payload_read=False,production_runtime_verified=True,
            independent_runtime=dict(reference=self.reference,acceptance=self.view.acceptance,completion=self.completion,
                revision=str(directory/'revision.json'),revision_sha256=sha256_file(directory/'revision.json')))
        self.audit['code'].update(self.binding['code']);self.audit['evidence'][artifact_path(self.runtime)]=sha256_file(self.runtime)
        self.write(Path(spec['audit']),self.audit);spec['audit_sha256']=sha256_file(Path(spec['audit']))
        return spec

    def test_explicit_prepare_spec_and_controller_preserve_original_target(self):
        spec=self.spec();before=file_stats(self.main)
        plan,config,parent=check_spec(spec);contract=make_contract(self.output)
        self.assertEqual(contract['independent_runtime'],self.reference)
        self.assertEqual(plan['independent_runtime'],self.reference)
        self.assertEqual((plan['parent_step'],plan['updates'],plan['local_budget_gib']),(57000,2,192))
        self.assertEqual(parent,self.parent);self.assertEqual(config,self.config)
        self.assertEqual(file_stats(self.main),before)
        self.assertFalse(Path(plan['scratch']).exists())

    def test_native_source_mismatch_and_deferred_budget_rejected_before_output(self):
        self.nc['independent_runtime']['runtime_contract_sha256']='0'*64
        self.write(self.native/'contract.json',self.nc)
        # Copy because nc/reference originally shared the same fixture object.
        self.reference=dict(control_dir=str(self.control),runtime_contract_sha256=sha256_file(self.runtime));self.binding['reference']=self.reference
        with self.assertRaisesRegex(ValueError,'Native/resume'):self.plan()
        self.nc['independent_runtime']=self.reference;self.write(self.native/'contract.json',self.nc)
        self.mc['shared_storage']['nfs_pools']['final_resume']['budget_bytes']=16*2**20;self.write(self.main/'contract.json',self.mc)
        with self.assertRaisesRegex(ValueError,'reservation'):self.plan()
        self.assertFalse(self.output.exists())

    def test_final_spec_routes_explicit_source_and_rejects_changed_parent(self):
        spec=self.spec();cp=Path(spec['parent_checkpoint']);proof=json.loads(Path(spec['parent_proof']).read_text());meta=json.loads((cp/'meta.json').read_text())
        audit=deepcopy(self.audit);audit['checkpoint_certificate']=spec['parent_proof']
        with patch('scripts.run_restored_final_resume.completed_main',return_value=(cp,proof,meta,audit)) as completed,patch('scripts.run_restored_final_resume.fresh_inputs',return_value=spec['input_stats']):
            produced=materialize_spec(self.output,spec['plan'])
        completed.assert_called_once_with(spec['plan']['main_run'],self.reference)
        self.assertEqual(produced['parent_stats'],spec['parent_stats'])
        (cp/'__0_0.distcp').write_bytes(b'changed')
        with self.assertRaises(ValueError):check_spec(produced)

    def test_incomplete_audit_or_different_runtime_cannot_be_used_as_acceptance(self):
        self.spec()
        faults=[('status','prepared_independent_prefix_audit_not_runtime_accepted'),('production_runtime_verified',False),('parent_payload_read',True)]
        for key,value in faults:
            bad=deepcopy(self.audit);bad[key]=value
            with self.assertRaisesRegex(ValueError,'runtime/exit'):verify_final_audit(bad,self.main,self.parent['id'],independent_runtime=self.reference)
        for key in ['reference','acceptance','completion']:
            bad=deepcopy(self.audit);bad['independent_runtime'][key]={}
            with self.assertRaisesRegex(ValueError,'runtime/exit'):verify_final_audit(bad,self.main,self.parent['id'],independent_runtime=self.reference)
        self.completion=None
        with self.assertRaisesRegex(ValueError,'runtime/exit'):verify_final_audit(self.audit,self.main,self.parent['id'],independent_runtime=self.reference)

    def test_revision_or_publication_mutation_is_rejected(self):
        self.spec();directory=self.control/'final_source_v1'
        self.write(directory/'prepared.json',{})
        with self.assertRaisesRegex(ValueError,'publication'):verify_final_audit(self.audit,self.main,self.parent['id'],independent_runtime=self.reference)
        self.write(directory/'revision.json',{'changed':True})
        with self.assertRaisesRegex(ValueError,'runtime/exit'):verify_final_audit(self.audit,self.main,self.parent['id'],independent_runtime=self.reference)

    def test_missing_runtime_evidence_or_code_is_not_accepted(self):
        spec=self.spec();bad=deepcopy(spec);bad['plan']['inputs'].pop(str(self.runtime));bad['plan_id']=identity(bad['plan'])
        with self.assertRaisesRegex(ValueError,'independent runtime code/input'):check_spec(bad)
        bad=deepcopy(spec);bad['plan']['code'].pop(source.CODE[0]);bad['plan_id']=identity(bad['plan'])
        with self.assertRaisesRegex(ValueError,'independent runtime code/input'):check_spec(bad)
        bad=deepcopy(self.audit);bad['evidence'].pop(artifact_path(self.runtime))
        with self.assertRaisesRegex(ValueError,'runtime/exit'):verify_final_audit(bad,self.main,self.parent['id'],independent_runtime=self.reference)
        bad=deepcopy(self.audit);bad['code'].pop(source.CODE[0])
        with self.assertRaisesRegex(ValueError,'implementation'):verify_final_audit(bad,self.main,self.parent['id'],independent_runtime=self.reference)

    def test_independent_receipt_never_silently_uses_legacy_audit_gate(self):
        self.spec()
        with self.assertRaisesRegex(ValueError,'final57k8rank'):verify_final_audit(self.audit,self.main,self.parent['id'])
        bad=deepcopy(self.audit);bad['counts']['successful_target_ranks']=list(range(7))
        with self.assertRaisesRegex(ValueError,'final57k8rank'):verify_final_audit(bad,self.main,self.parent['id'],independent_runtime=self.reference)
