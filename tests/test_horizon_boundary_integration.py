"""Production wrapper with real CPU pidfds, file locking, copy and sealing.

Original audit/payloads and capacity are synthetic. Process capture is replaced
only because the ten small fixture processes are not production torchrun.
This is not an actual 3B or CUDA resume validation.
"""
import json
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

import test_prepare_horizon_probe as preparation
import test_horizon_boundary_handoff as processes
from common.data_contracts import resolve, sha256_file
from scripts import horizon_boundary_handoff as boundary
from scripts.horizon_process_handoff import process_record
from scripts.seal_horizon_handoff import require_parent_absent
from train.final_resume_probe import file_stats


class BoundaryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.f = preparation.ProbePreparationTests(methodName='runTest')
        self.addCleanup(self.f.doCleanups); self.f.setUp()
        self.p = processes.BoundaryTransactionTests(methodName='runTest')
        self.addCleanup(self.p.doCleanups); self.p.setUp()
        temporary = tempfile.TemporaryDirectory(prefix='boundary-wrapper-', dir=resolve('runs'))
        self.addCleanup(temporary.cleanup)
        self.destination = Path(temporary.name); self.destination.rmdir()
        self.source = self.f.source
        self.capture = dict(self.p.capture, status='observed_live_not_stopped', run=str(self.source))
        self.f.capture_path.write_text(json.dumps(self.capture))
        (self.source/'training/training_contract.json').write_text(json.dumps(self.f.meta['training_contract']))
        (self.source/'retention.lock').touch()
        self.f.draft.update(intended_run=str(self.destination), expected_checkpoint_bytes=self.f.proof['bytes'],
                            tiered_retention={'run_id':'b'*32})
        self.f.write_draft()
        self.checkpoint = self.source/'training/ckpt_0000500'
        code = {'scripts/horizon_boundary_handoff.py','scripts/horizon_process_handoff.py',
                'scripts/protect_horizon_parent.py','scripts/seal_horizon_handoff.py',
                'scripts/horizon_storage_plan.py','scripts/audit_checkpoint_retention.py'}
        inputs = [self.f.draft_path,self.f.capture_path,self.source/'contract.json',self.source/'main.json',
                  self.source/'training/training_contract.json',self.checkpoint/'meta.json',
                  self.source/'checkpoint_certificates/ckpt_0000500.json']
        self.plan = dict(purpose='approved_horizon_checkpoint_handoff',source_stop_authorized=True,
            target_steps=35000,max_seconds=600,source=str(self.source),destination=str(self.destination),
            draft=str(self.f.draft_path),capture=str(self.f.capture_path),
            output=str(self.destination.with_name(self.destination.name+'-evidence')),checkpoint_step=500,
            code={p:sha256_file(resolve(p)) for p in code},inputs={str(p):sha256_file(p) for p in inputs})
        # Evidence is inside a separate temporary owner, never a production run.
        evidence = tempfile.TemporaryDirectory(prefix='boundary-evidence-',dir=resolve('runs'))
        self.addCleanup(evidence.cleanup)
        self.plan['output']=str(Path(evidence.name)/'transaction')
        self.path=self.source/'boundary.json';self.write_plan()
        self.storage=dict(issues=[],residues=[],nfs_resume_candidates=['ckpt_0000500'])
        def reap_then_check(capture,source):
            for p in self.p.processes.values():p.wait(timeout=5)
            return require_parent_absent(capture,source)
        for name,options in [
            ('scripts.horizon_boundary_handoff.capture_producer',dict(return_value=self.capture)),
            ('scripts.horizon_boundary_handoff.enforce_runtime_space',dict(return_value={'nfs_remaining_bytes':0})),
            ('scripts.audit_checkpoint_retention.audit_run',dict(return_value=self.storage)),
            ('scripts.horizon_boundary_handoff.require_parent_absent',dict(side_effect=reap_then_check))]:
            patcher=patch(name,**options);mock=patcher.start();self.addCleanup(patcher.stop)
            if name.endswith('enforce_runtime_space'):self.capacity=mock
        self.original_stats=file_stats(self.checkpoint)

    def write_plan(self):self.path.write_text(json.dumps(self.plan))
    def run_handoff(self):return boundary.handoff(self.path)
    def assert_paused_without_termination(self):
        self.assertEqual({process_record(p.pid)['state'] for p in self.p.processes.values()},{'T'})
        events=[json.loads(s) for s in (Path(self.plan['output'])/'signals.jsonl').read_text().splitlines()]
        self.assertFalse(any(r.get('signal') in (signal.SIGTERM,signal.SIGKILL) for r in events))

    def test_copy_then_seal_with_actual_cpu_process_termination(self):
        self.run_handoff()
        self.assertEqual(file_stats(self.checkpoint),self.original_stats)
        receipt=json.loads((self.destination/'parent_handoff.json').read_text())
        self.assertEqual(receipt['parent_metadata'],self.f.meta)
        self.assertEqual(receipt['parent_stats'],file_stats(self.destination/'parent_checkpoint/ckpt_0000500'))
        state=json.loads((Path(self.plan['output'])/'state.json').read_text())
        self.assertEqual(state['phase'],'handoff_sealed')
        self.assertTrue(all(p.returncode==-signal.SIGTERM for p in self.p.processes.values()))
        self.assertFalse((self.destination/'training').exists())

    def test_changed_bound_input_is_rejected_before_namespace_or_signal(self):
        (self.source/'main.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'Source evidence changed'):self.run_handoff()
        self.assertFalse(self.destination.exists())
        self.assertFalse(Path(self.plan['output']).exists())
        self.assertTrue(all(process_record(p.pid)['state']!='T' for p in self.p.processes.values()))

    def test_capacity_failure_before_signals_preserves_original(self):
        self.capacity.side_effect=RuntimeError('fixture capacity unavailable')
        with self.assertRaisesRegex(RuntimeError,'capacity unavailable'):self.run_handoff()
        self.assertFalse(Path(self.plan['output']).exists())
        self.assertTrue(all(process_record(p.pid)['state']!='T' for p in self.p.processes.values()))
        self.assertEqual(file_stats(self.checkpoint),self.original_stats)

    def test_unsettled_retention_never_copies_or_terminates(self):
        self.storage['residues']=['fixture.copying']
        with self.assertRaisesRegex(ValueError,'retention is not settled'):self.run_handoff()
        self.assert_paused_without_termination()
        self.assertFalse((self.destination/'parent_checkpoint').exists())

    def test_payload_corruption_fails_real_sha_before_original_termination(self):
        payload=self.checkpoint/'__0_0.distcp'
        payload.write_bytes(b'x'*payload.stat().st_size)
        with self.assertRaises(ValueError):self.run_handoff()
        self.assert_paused_without_termination()
        self.assertTrue((self.destination/'parent_checkpoint/ckpt_0000500.copying').exists())
        self.assertFalse((self.destination/'parent_checkpoint/ckpt_0000500.receipt.json').exists())

    def test_post_termination_seal_failure_keeps_verified_copy_and_original(self):
        with patch.object(boundary,'seal_handoff',side_effect=ValueError('fixture seal failure')):
            with self.assertRaisesRegex(ValueError,'seal failure'):self.run_handoff()
        self.assertEqual(file_stats(self.checkpoint),self.original_stats)
        proof=json.loads((self.destination/'parent_checkpoint/ckpt_0000500.receipt.json').read_text())
        self.assertEqual(proof['status'],'verified_protected_parent')
        failure=json.loads((Path(self.plan['output'])/'failure.json').read_text())
        self.assertEqual(failure['phase'],'original_processes_exited')
        self.assertFalse(failure['automatic_restart'])


if __name__=='__main__':unittest.main()
