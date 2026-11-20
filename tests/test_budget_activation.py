"""Real lineage/hash and synthetic8GPU report activation gate; no CUDA/launch.

Retention-owner creation is isolated here; real four-owner copy/audit tests are
separate. The fixture cannot serve as production distributed acceptance.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest

from common.data_contracts import resolve,sha256_file,identity,atomic_json
from scripts.activate_budget_training import activation
from scripts.run_budget_probe import collect
from scripts.tiered_checkpoint_retention import make_policy
import test_budget_probe_collection as fixture_module

@unittest.skipUnless(os.environ.get('DENSE_BUDGET_LINEAGE_FIXTURE'),'Explicit prefix fixture required')
class BudgetActivationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_module.BudgetCollectionTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;self.root=f.fixture.root;self.source=f.fixture.source
        self.report=collect(f.path)
        atomic_json(f.root/'.done',dict(plan_id=identity(f.plan),report_sha256=sha256_file(f.root/'report.json')))
        self.draft=deepcopy(f.fixture.draft);self.path=self.root/'activation_draft.json'
        atomic_json(self.source/'contract.json',dict(fixture=True))
        self.auth=self.root/'authorization.json';self.boundary=f.plan['parent_step']
        atomic_json(self.auth,dict(authorized=True,retention_transfer_authorized=True,
            checkpoint_boundary_stop_authorized=True,target_steps=50000,boundary_step=self.boundary,
            source_run=str(self.source),new_run=str(self.root)))
        config=f.fixture.fixture.config
        atomic_json(self.root/'main.json',config)
        policy=make_policy(self.root,Path(f.plan['diagnostic_temp_root']).parent,2**20,run_id='a'*32)
        policy.update(version=2,nfs_keep=3,adaptive_local_pruning=True,protected_parent_budget_bytes=2**20)
        revision=self.root/'revision.json';atomic_json(revision,dict(authorized=True,nfs_keep=3,local_max_keep=15,
            adaptive_local_oldest_first=True,management_run=str(self.root),local_namespace=policy['local_root']))
        self.draft.update(requested_target_steps=50000,requested_target_tokens=104857600000,target_steps=50000,
            main_run=str(self.root),boundary_step=self.boundary,source_contract_sha256=sha256_file(self.source/'contract.json'),
            generation_steps=f.child['monitored_evaluation']['generation_steps'],
            configurations={'main.json':sha256_file(self.root/'main.json')},tiered_retention=policy,
            retention_policy='nfs_latest_with_ephemeral_history',archive_required_before_prune=True,
            protected_parent_budget_bytes=2**20,durable_budget_bytes=policy['nfs_budget_bytes'],
            retention_revision=dict(authorization=str(revision),authorization_sha256=sha256_file(revision)))
        # Activate a separately prepared draft; original diagnostic plan/input
        # evidence remains unchanged, as it must in actual execution.
        atomic_json(self.path,self.draft)
        self.patcher=patch('scripts.activate_budget_training.BudgetRetention');self.patcher.start();self.addCleanup(self.patcher.stop)

    def run_activation(self):
        return activation(self.root,self.path,self.fixture.fixture.capture_path,self.auth)

    def test_actual_lineage_and_synthetic_report_publish50k_without_launch(self):
        result=self.run_activation();c=json.loads((self.root/'contract.json').read_text())
        self.assertEqual(result['target_steps'],50000);self.assertEqual(c['target_tokens'],104857600000)
        self.assertEqual(c['budget_execution']['parent_step'],self.boundary)
        self.assertFalse((self.root/'pid').exists())
        self.assertEqual(c['budget_validation_report'],str(self.root/'horizon_probe/report.json'))

    def test_wrong_boundary_approval_rejects_before_execution_contract(self):
        value=json.loads(self.auth.read_text());value['boundary_step']+=500;atomic_json(self.auth,value)
        with self.assertRaises(ValueError):self.run_activation()
        self.assertFalse((self.root/'contract.json').exists())

    def test_changed_probe_payload_rejected_even_with_success_marker(self):
        path=next(Path(self.fixture.plan['payload_roots']['continuous']).rglob('*.distcp'))
        path.write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.run_activation()
        self.assertFalse((self.root/'contract.json').exists())
