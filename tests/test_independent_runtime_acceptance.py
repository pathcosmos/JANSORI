"""Replay real archived evidence with synthetic NCCL/tensor proof fixtures."""
import json
from pathlib import Path
import pytest
from common.data_contracts import sha256_file
from test_sealed_independent_resume_result import case,diagnostic
from scripts.seal_independent_resume_result import seal
from train.independent_runtime_contract import verify_acceptance


def test_runtime_acceptance_replays_archive_and_pins_tested_code(diagnostic,monkeypatch):
    f,c,_=diagnostic;seal(f.root)
    ref=dict(run=str(f.root),manifest_sha256=sha256_file(f.root/'accepted_evidence/manifest.json'),
        verified_sha256=sha256_file(f.root/'accepted_evidence/verified.json'))
    assert verify_acceptance(c.root,ref)['parent_training_contract_id']==c.candidate['id']
    ref['manifest_sha256']='0'*64
    with pytest.raises(ValueError,match='acceptance pin'):verify_acceptance(c.root,ref)


def test_missing_actual_independent_acceptance_is_not_cpu_preparation(diagnostic):
    f,c,_=diagnostic
    ref=dict(run=str(f.root),manifest_sha256='0'*64,verified_sha256='0'*64)
    with pytest.raises(FileNotFoundError):verify_acceptance(c.root,ref)
