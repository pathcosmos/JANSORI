from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.extended_horizon_process_handoff import capture_horizon_producer


class ExtendedCaptureTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.project=Path(temp.name);self.root=self.project/'runs'/'source';self.root.mkdir(parents=True)
        (self.root/'main.json').write_text('{}')
        common=['--run-dir',str(self.root),'--config',str(self.root/'main.json'),'--attempt','one']
        def record(pid,argv,ppid=1):
            return dict(pid=pid,argv=argv,cwd=str(self.project),ppid=ppid,
                        start_ticks=100+pid,argv_sha256='a'*64,uid=1,state='S')
        self.rows={101:record(101,['python','scripts/supervise_horizon_training.py','--run-dir',str(self.root)]),
                   102:record(102,['python','-m','torch.distributed.run','--module','train.monitored_horizon',*common])}
        for rank in range(8):self.rows[110+rank]=record(110+rank,['python','-m','train.monitored_horizon',*common],102)
        self.topology={102:set(range(110,118))}
        self.env={pid:f'LOCAL_RANK={pid-110}\0'.encode() for pid in range(110,118)}
        self.real_read=Path.read_bytes

    def capture(self,record_fn=None):
        def read(path):
            if path.name=='environ' and str(path).startswith('/proc/'):
                return self.env[int(path.parent.name)]
            return self.real_read(path)
        with patch('scripts.extended_horizon_process_handoff.process_record',side_effect=record_fn or (lambda pid:deepcopy(self.rows[pid]))), \
             patch('scripts.extended_horizon_process_handoff.children',side_effect=lambda pid:self.topology.get(pid,set())), \
             patch.object(Path,'read_bytes',read):
            return capture_horizon_producer(self.root,101,102)

    def test_exact_current_supervisor_worker_and_all_ranks(self):
        result=self.capture()
        self.assertEqual(set(result['processes']),{'supervisor','worker',*[f'rank{i}' for i in range(8)]})
        self.assertEqual(result['status'],'observed_live_not_stopped')
        self.assertEqual(result['attempt'],'one')

    def test_wrong_entry_rank_parent_and_descendant_rejected(self):
        original=deepcopy(self.rows)
        for kind in ['supervisor','worker','rank','ppid','attempt']:
            self.rows=deepcopy(original)
            if kind=='supervisor':self.rows[101]['argv'][1]='scripts/supervise_main_training.py'
            if kind=='worker':self.rows[102]['argv'][self.rows[102]['argv'].index('train.monitored_horizon')]='train.monitored'
            if kind=='rank':self.rows[110]['argv'][2]='train.monitored'
            if kind=='ppid':self.rows[110]['ppid']=999
            if kind=='attempt':self.rows[110]['argv'][-1]='different'
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.capture()
        self.rows=original;self.topology[110]={999}
        with self.assertRaises(ValueError):self.capture()

    def test_duplicate_missing_or_invalid_rank_rejected(self):
        for value in [b'LOCAL_RANK=1\0',b'LOCAL_RANK=8\0',b'LOCAL_RANK=0\0LOCAL_RANK=0\0',b'OTHER=1\0']:
            self.env[110]=value
            with self.assertRaises(ValueError):self.capture()
        self.topology[102].remove(117)
        with self.assertRaises(ValueError):self.capture()

    def test_reused_pid_rejected_at_final_recheck(self):
        calls={}
        def record(pid):
            calls[pid]=calls.get(pid,0)+1;row=deepcopy(self.rows[pid])
            if pid==101 and calls[pid]>1:row['start_ticks']+=1
            return row
        with self.assertRaisesRegex(ValueError,'changed during capture'):self.capture(record)


if __name__=='__main__':unittest.main()
