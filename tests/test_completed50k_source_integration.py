"""Actual sealed16k history plus tiny synthetic50k checkpoint, no CUDA.

The future completed source inspector and all ledger/identity collectors execute
without mocking. The synthetic checkpoint is not a valid model or GPU proof.
"""
import json
from pathlib import Path
import pytest
from common.data_contracts import sha256_file
from scripts.inspect_budget_completed_source import inspect_completed_source
from test_completed50k_ledger_integration import synthetic_completed, SOURCE

pytestmark=pytest.mark.skipif(not SOURCE,reason='Explicit immutable16k fixture required')


def test_full_source_inspection_and_payload_damage(synthetic_completed,monkeypatch):
    main=synthetic_completed;cp=main/'training/ckpt_0050000'
    cert=main/'checkpoint_certificates/ckpt_0050000.json';proof=json.loads(cert.read_text())
    for row in proof['files']:
        p=cp/row['path'];p.parent.mkdir(parents=True,exist_ok=True)
        if row['path']!='meta.json':p.write_bytes(('synthetic '+row['path']).encode())
        row.update(bytes=p.stat().st_size,sha256=sha256_file(p))
    proof['bytes']=sum(r['bytes'] for r in proof['files'])
    cert.write_text(json.dumps(proof)+'\n')
    original=Path.open
    def deny_parent(path,*args,**kwargs):
        if 'parent_checkpoint' in path.parts:raise AssertionError('Parent payload access')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',deny_parent)
    result=inspect_completed_source(main,workers=2)
    assert result['full_payload_sha_verified'] and result['source_process_absence_observed']
    assert result['committed_tokens']==104857600000 and result['committed_step_rank_pairs']==400000
    assert not any(result[k] for k in ['future_source_launch_locked','access_isolation_verified',
        'actual8gpu_verified','retirement_authorized','goal_complete'])
    tensor=next(p for p in cp.rglob('*.distcp'));old=tensor.read_bytes()
    try:
        tensor.write_bytes(b'X'*len(old))
        with pytest.raises(ValueError,match='SHA differs'):inspect_completed_source(main,workers=2)
    finally:tensor.write_bytes(old)
