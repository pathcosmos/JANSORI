"""Discover known legacy and50k diagnostic owners before restoring access.

Read-only. A process listing is a point-in-time observation, not a launch lock
or proof that a different, as-yet-unreviewed worker is absent.
"""
from pathlib import Path
from common.data_contracts import resolve
from scripts.horizon_process_handoff import process_record

MODULES={'train.independent_resume_probe','train.budget_final_resume_probe'}
WORKERS={'run_independent_resume.py','run_budget_final_resume.py'}


def owns(args,cwd,root):
    root=resolve(root);cwd=Path(cwd)
    if any(a in MODULES for a in args):
        if args.count('--spec')!=1:raise ValueError('Ambiguous diagnostic spec argument')
        i=args.index('--spec')
        if i+1>=len(args):raise ValueError('Missing diagnostic spec path')
        return (cwd/args[i+1]).resolve()==root/'spec.json'
    if '--worker' in args and any(Path(a).name in WORKERS for a in args):
        if args.count('--run-dir')!=1:raise ValueError('Ambiguous diagnostic worker root')
        i=args.index('--run-dir')
        if i+1>=len(args):raise ValueError('Missing diagnostic worker root')
        return (cwd/args[i+1]).resolve()==root
    return False


def running(root):
    root=resolve(root);found=[]
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            stat=(proc/'stat').read_text().rsplit(')',1)[1].split()
            if stat[0] in ('Z','X'):continue
            args=[s.decode() for s in (proc/'cmdline').read_bytes().split(b'\0') if s]
            if not (any(a in MODULES for a in args) or
                    ('--worker' in args and any(Path(a).name in WORKERS for a in args))):
                continue
            row=process_record(int(proc.name))
            if owns(row['argv'],row['cwd'],root):found.append(row)
        except (FileNotFoundError,ProcessLookupError):
            continue
        except (PermissionError,UnicodeError) as exc:
            raise RuntimeError('Cannot inspect potential diagnostic process') from exc
    return found
