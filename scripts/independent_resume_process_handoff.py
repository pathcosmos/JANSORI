"""Read-only identity capture for the current57k source for independent resume preparation.

The existing pidfd signalling/transaction primitives remain unchanged. This
adapter does not signal, arm a watcher, modify configuration or start a process.
"""
from pathlib import Path

from scripts.horizon_process_handoff import process_record, children, same_identity


def capture_extended_producer(root, supervisor_pid, worker_pid):
    root=Path(root).resolve(strict=True)
    records=dict(supervisor=process_record(supervisor_pid),worker=process_record(worker_pid))
    def argument(row,flag):
        args=row['argv']
        if args.count(flag)!=1 or args.index(flag)+1==len(args):
            raise ValueError('Missing/ambiguous '+flag)
        return args[args.index(flag)+1]
    def command_path(row,flag):
        return (Path(row['cwd'])/argument(row,flag)).resolve(strict=True)
    supervisor=records['supervisor']
    expected=root.parents[1]/'scripts/supervise_extended_training.py'
    if not any((Path(supervisor['cwd'])/a).resolve()==expected for a in supervisor['argv']):
        raise ValueError('Unexpected57k supervisor entry')
    for row in records.values():
        if command_path(row,'--run-dir')!=root:raise ValueError('Producer run path differs')
    worker=records['worker']
    if ('torch.distributed.run' not in worker['argv'] or
            argument(worker,'--module')!='train.monitored_extended_horizon'):
        raise ValueError('Unexpected57k torchrun entry')
    attempt=argument(worker,'--attempt')
    if command_path(worker,'--config')!=root/'main.json':raise ValueError('Worker config differs')
    pids=children(worker_pid)
    if len(pids)!=8:raise ValueError('Expected exactly eight direct ranks')
    for pid in pids:
        row=process_record(pid)
        # Inspect only LOCAL_RANK. Never write environment contents to evidence.
        env=(Path('/proc')/str(pid)/'environ').read_bytes().split(b'\0')
        ranks=[e.split(b'=',1)[1] for e in env if e.startswith(b'LOCAL_RANK=')]
        if len(ranks)!=1 or not ranks[0].isdigit() or not 0<=int(ranks[0])<8:
            raise ValueError('Missing/invalid LOCAL_RANK')
        name='rank'+str(int(ranks[0]))
        if (name in records or 'train.monitored_extended_horizon' not in row['argv'] or
                argument(row,'--attempt')!=attempt or command_path(row,'--run-dir')!=root or
                command_path(row,'--config')!=root/'main.json' or
                row['ppid']!=worker_pid or children(pid)):
            raise ValueError('Rank ownership/command/topology differs')
        records[name]=row
    if len({r['pid'] for r in records.values()})!=10:
        raise ValueError('Process identities must be distinct')
    for row in records.values():
        if not same_identity(row,process_record(row['pid'])):
            raise ValueError('Process changed during capture')
    return dict(status='observed_live_not_stopped',run=str(root),attempt=attempt,processes=records)
