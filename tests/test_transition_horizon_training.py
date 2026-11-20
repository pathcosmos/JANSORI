"""Boundary wait/orchestration failure gates; no production signals or GPU work."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import transition_horizon_training as transition


class TransitionTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix='transition-test-');self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.source=self.root/'source';self.source.mkdir();(self.source/'training').mkdir();(self.source/'checkpoint_certificates').mkdir()
        self.capture=self.root/'capture.json'
        self.rows={n:dict(pid=i+100,start_ticks=1,argv_sha256='fixture',cwd=str(self.source),uid=0)
                   for i,n in enumerate(['supervisor','worker',*[f'rank{r}' for r in range(8)]])}
        self.capture.write_text(json.dumps({'processes':self.rows}))
        self.plan=dict(source=str(self.source),destination=str(self.root/'new'),capture=str(self.capture),
            boundary_step=8000,wait_max_seconds=7200,draft='fixture',authorization='fixture')
        (self.source/'state.json').write_text(json.dumps(dict(phase='running')))
        (self.source/'retention_state.json').write_text(json.dumps(dict(nfs_checkpoints=['ckpt_0007000','ckpt_0007500','ckpt_0008000'],uncertified_nfs=[])))
        m=patch.object(transition,'capture_producer',return_value={'processes':self.rows});m.start();self.addCleanup(m.stop)
        self.log=self.source/'training/log.jsonl'

    def test_future_boundary_needs_certificate_and_settled_retention(self):
        self.log.write_text('{"step":8000,"loss":2}\n{"step":')
        self.assertEqual(transition.ready(self.plan),(False,8000))
        (self.source/'checkpoint_certificates/ckpt_0008000.json').write_text('{}')
        self.assertEqual(transition.ready(self.plan),(True,8000))
        (self.source/'retention_state.json').write_text(json.dumps(dict(nfs_checkpoints=['ckpt_0006500','ckpt_0007000','ckpt_0007500'])))
        self.assertEqual(transition.ready(self.plan),(False,8000))

    def test_changed_producer_and_missed_boundary_are_not_restarted(self):
        self.log.write_text('{"step":8500,"loss":2}\n')
        with self.assertRaisesRegex(RuntimeError,'Missed exact boundary'):transition.ready(self.plan)
        self.log.write_text('{"step":8000,"loss":2}\n')
        observed=json.loads(self.capture.read_text());observed['processes']['worker']['start_ticks']=2
        with patch.object(transition,'capture_producer',return_value=observed):
            with self.assertRaisesRegex(ValueError,'producer changed'):transition.ready(self.plan)

    def test_wait_failure_preserves_evidence_without_stopping_source(self):
        output=self.root/'workflow'
        with patch.object(transition,'read_plan',return_value=(self.plan,output)),patch.object(transition,'sha256_file',return_value='fixture'),patch.object(transition,'event'),patch.object(transition,'ready',side_effect=RuntimeError('fixture producer failed')),patch.object(transition,'supersede_waiters') as stop:
            with self.assertRaisesRegex(RuntimeError,'producer failed'):transition.workflow(self.capture)
        stop.assert_not_called()
        failure=json.loads((output/'failure.json').read_text());self.assertEqual(failure['phase'],'await_checkpoint_boundary')
        self.assertFalse(failure['automatic_retry'])

    def test_actual_probe_failure_never_activates_or_launches_main(self):
        output=self.root/'workflow'
        with patch.object(transition,'read_plan',return_value=(self.plan,output)),patch.object(transition,'sha256_file',return_value='fixture'),patch.object(transition,'event'),patch.object(transition,'ready',return_value=(True,8000)),patch.object(transition,'supersede_waiters'),patch.object(transition,'boundary_plan',return_value=self.capture),patch.object(transition,'handoff'),patch.object(transition,'prepare'),patch.object(transition,'execute',side_effect=RuntimeError('fixture GPU comparison failed')),patch.object(transition,'activation') as activate,patch.object(transition,'spawn') as spawn:
            with self.assertRaisesRegex(RuntimeError,'comparison failed'):transition.workflow(self.capture)
        activate.assert_not_called();spawn.assert_not_called()
        failure=json.loads((output/'failure.json').read_text());self.assertEqual(failure['phase'],'actual8gpu_probe')

    def test_registration_preserves_other_jobs_and_rejects_duplicate(self):
        from unittest.mock import Mock
        logs=self.root/'logs';logs.mkdir();registry=logs/'independent_jobs.json'
        registry.write_text(json.dumps(dict(schema_version=1,jobs=[{'run_id':'preserved'}])))
        run=self.root/'native';run.mkdir();(run/'pid').write_text('123')
        process=Mock(pid=123);process.poll.return_value=None
        def fixture_resolve(path):
            p=Path(path);return p if p.is_absolute() else self.root/p
        with patch.object(transition,'resolve',side_effect=fixture_resolve):
            transition.register_started(self.root,str(run),process,'fixture.py',self.root/'console')
            with self.assertRaisesRegex(ValueError,'existing observer/native'):
                transition.register_started(self.root,str(run),process,'fixture.py',self.root/'console')
        self.assertEqual([s['run_id'] for s in json.loads(registry.read_text())['jobs']],['preserved','native'])


if __name__=='__main__':unittest.main()
