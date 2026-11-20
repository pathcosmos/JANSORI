"""No production signals. Invalid plans must fail before source inspection."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from scripts.extended_boundary_handoff import validate_plan,validate_controllers
from scripts.seal_extended_handoff import seal_extension
from train.extended_baseline_worker import validate_configuration

class ExtendedBoundaryPlanTests(unittest.TestCase):
    def test_unapproved_target_boundary_or_unbound_code_rejected(self):
        with self.assertRaisesRegex(ValueError,'Explicit frozen'):validate_plan({})
        plan=dict(purpose='approved_extension_boundary_handoff',execution_authorized=True,
                  boundary_step=10000,target_steps=57000,max_seconds=600)
        for key,value in [('execution_authorized',False),('boundary_step',10500),('target_steps',35000),('max_seconds',3600)]:
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'Explicit frozen'):
                validate_plan(dict(plan,**{key:value}))
        plan.update(source='runs/3b_full_35k_v1',destination='runs/3b_full_57k_v2',
                    output='runs/3b_57k_transition_v2/boundary10000',draft='unused',capture='unused',
                    controllers='unused',authorization='unused',code={})
        with self.assertRaisesRegex(ValueError,'implementation binding'):validate_plan(plan)
        plan['destination']='runs/3b_full_35k_v1'
        with self.assertRaisesRegex(ValueError,'namespaces'):validate_plan(plan)

    def test_missing_controllers_and_unapproved_seal_boundary_rejected(self):
        with self.assertRaisesRegex(ValueError,'seven'):validate_controllers({})
        for step in (9500,10500):
            with self.assertRaisesRegex(ValueError,'authorized10000'):
                seal_extension('runs/3b_full_35k_v1','runs/3b_full_57k_v2',{}, {}, owner_id='a'*32,step=step)

    def test_exact_defaults_for_waiting_runtime_and_serving(self):
        from common.data_contracts import resolve
        from scripts.extended_boundary_handoff import CONTROLLER_SCOPE
        rows={}
        for i,(name,(entry,flag,target)) in enumerate(CONTROLLER_SCOPE.items()):
            args=['python','-u',entry,'--run']
            if name not in ('runtime','serving'):args.extend([flag,target])
            rows[name]=dict(pid=1000+i,start_ticks=1,argv=args,cwd=str(resolve('.')),uid=1100,argv_sha256=str(i))
        observed={r['pid']:r for r in rows.values()}
        with patch('scripts.extended_boundary_handoff.process_record',side_effect=lambda p:observed[p]),patch('scripts.extended_boundary_handoff.children',return_value=set()):
            validate_controllers(rows)
            from scripts.extended_boundary_handoff import WaitingControllerBusy
            with patch('scripts.extended_boundary_handoff.children',return_value={9999}):
                with self.assertRaises(WaitingControllerBusy):validate_controllers(rows)
            rows['runtime']['argv'].extend(['--export-run','artifacts/unrelated'])
            with self.assertRaisesRegex(ValueError,'target differs'):validate_controllers(rows)

    def test_baseline_not_a_general_training_entry(self):
        cfg=dict(max_steps=35000,stop_after=10002,keep_ckpts=57001,save_final=False)
        c=dict(purpose='extension_baseline_validation')
        with patch('scripts.plan_main_training.geometry',return_value={'fixture':True}):
            self.assertEqual(validate_configuration(cfg,c),{'fixture':True})
            for key,value in [('max_steps',57000),('stop_after',None),('save_final',True),('keep_ckpts',3)]:
                with self.subTest(key=key),self.assertRaisesRegex(ValueError,'restricted'):
                    validate_configuration(dict(cfg,**{key:value}),c)
            with self.assertRaises(ValueError):validate_configuration(cfg,dict(purpose='main_training'))
