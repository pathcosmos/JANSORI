"""Read-only final50k source audit without opening ancestor checkpoint payloads.

This verifies completion metadata, four-generation ledger and every file SHA of
the exact own NFS final checkpoint. It does not isolate dependencies, restore
GPU state, lock future producer launches, or authorize any retirement.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,sha256_file
from scripts.audit_budget_completed_ledgers import audit_completed
from scripts.completed_budget_storage import active_source_processes
from scripts.inspect_budget_completed_identity import inspect as inspect_identity
from scripts.tiered_checkpoint_retention import exact_path,inventory
from train.final_resume_probe import file_stats


def inspect_completed_source(main, *, workers=4):
    main=exact_path(resolve(main))
    if type(workers) is not int or not 1<=workers<=16:
        raise ValueError('Hash workers must be an integer between1 and16')
    if active_source_processes(main):
        raise ValueError('Training source must be stopped before final inspection')
    identity=inspect_identity(main,final=True)
    ledger=audit_completed(main)
    checkpoint=main/'training/ckpt_0050000'
    cert=main/'checkpoint_certificates/ckpt_0050000.json'
    before_cert=sha256_file(cert)
    proof=json.loads(cert.read_text())
    if (proof.get('step')!=50000 or proof.get('training_contract_id')!=identity['training_contract_id'] or
            exact_path(resolve(proof['checkpoint']))!=checkpoint):
        raise ValueError('Final certified own checkpoint differs')
    before=file_stats(checkpoint)
    inventory(checkpoint,proof,hashes=True,workers=workers)
    if (file_stats(checkpoint)!=before or sha256_file(cert)!=before_cert):
        raise ValueError('Final checkpoint/certificate changed during verification')
    for path,digest in ledger['evidence'].items():
        if sha256_file(resolve(path))!=digest:
            raise ValueError('Final ledger evidence changed during payload hashing')
    after=inspect_identity(main,final=True)
    if identity!=after or active_source_processes(main):
        raise ValueError('Completion identity or source-process absence changed')
    return dict(status='verified_stopped_50k_ledger_and_own_checkpoint_sha',
        main_run=str(main),checkpoint=str(checkpoint),certificate_sha256=before_cert,
        training_contract_id=identity['training_contract_id'],through_step=50000,
        committed_tokens=ledger['counts']['committed_tokens'],
        committed_step_rank_pairs=ledger['counts']['committed_step_rank_pairs'],
        source_tokens=ledger['counts']['source_tokens'],files=len(proof['files']),bytes=proof['bytes'],
        parent_payload_read=False,source_process_absence_observed=True,
        future_source_launch_locked=False,full_payload_sha_verified=True,
        access_isolation_verified=False,actual8gpu_verified=False,
        retirement_authorized=False,goal_complete=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--main-run',required=True);p.add_argument('--workers',type=int,default=4)
    a=p.parse_args();print(json.dumps(inspect_completed_source(a.main_run,workers=a.workers),ensure_ascii=False,indent=2))
