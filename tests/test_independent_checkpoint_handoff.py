"""Real prefix recomputation plus synthetic file protocol; no GPU acceptance."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile

import pytest

from common.data_contracts import resolve, sha256_file
from train.independent_checkpoint_handoff import review_handoff
from train.extended_handoff import load_handoff


@pytest.fixture
def case(monkeypatch):
    if not os.environ.get('DENSE_EXTENSION_LINEAGE_FIXTURE'):
        pytest.skip('Explicit immutable prefix fixture required')
    from test_extended_handoff import ExtendedHandoffIntegrationTests
    c=ExtendedHandoffIntegrationTests('test_valid_protocol_fixture_recomputes_actual_prefix')
    c.setUp()
    # These protocol fixtures do not own the real production1024GiB pool.
    # Requiring a second empty pool would double reserve server capacity.
    monkeypatch.setattr('train.independent_checkpoint_handoff.enforce_shared_space',
                        lambda *args: {'fixture_protocol_only': True})
    try:
        child=c.root/'training/ckpt_0010000';child.mkdir(parents=True)
        c.write(child/'meta.json',dict(step=10000,world_size=8,tokens_per_step=2097152,
                                     config=c.config,training_contract=c.candidate))
        for n in ['.complete','.metadata',*[f'rng_rank{i}.pt' for i in range(8)],
                  *[f'__{i}_0.distcp' for i in range(8)]]:
            (child/n).write_bytes(('synthetic '+n).encode())
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(child.iterdir())]
        (c.root/'checkpoint_certificates').mkdir()
        cert=c.root/'checkpoint_certificates/ckpt_0010000.json'
        c.write(cert,dict(status='passed_main_checkpoint_storage',checkpoint=str(child),step=10000,
                         training_contract_id=c.candidate['id'],files=files,bytes=sum(r['bytes'] for r in files)))
        c.write(c.root/'main.json',c.config)
        c.contract['configurations']={'main.json':sha256_file(c.root/'main.json')}
        c.write(c.root/'contract.json',c.contract)
        revision=c.root/'independent_review';revision.mkdir()
        c.revision=dict(purpose='independent_nfs_resume_input_review_v1',source_run=str(c.root),
                        production_adoption_authorized=False,source_contract_sha256=sha256_file(c.root/'contract.json'),
                        child=dict(step=10000,checkpoint=str(child),certificate_sha256=sha256_file(cert)),
                        local_proof_backups={},code={'train/independent_checkpoint_handoff.py':sha256_file(resolve('train/independent_checkpoint_handoff.py'))})
        c.review_path=revision/'revision.json';c.write(c.review_path,c.revision)
        c.child=child;c.cert=cert
        yield c
    finally:
        c.doCleanups()


def review(c):
    return review_handoff(c.root,c.contract,c.config,c.review_path)


def rebind(c):
    c.rebind();c.write(c.root/'contract.json',c.contract)
    c.revision['source_contract_sha256']=sha256_file(c.root/'contract.json')
    c.write(c.review_path,c.revision)


def test_absent_parent_payload_is_not_needed_by_review(case):
    c=case;shutil.rmtree(c.parent)
    with pytest.raises(FileNotFoundError):load_handoff(c.root,c.contract,c.config)
    result=review(c)
    assert result['checkpoint']['step']==10000
    assert result['checkpoint']['full_payload_sha_refreshed']
    assert result['training_contract_id']==c.candidate['id']
    assert not result['parent_payload_read']
    assert not result['actual8gpu_independent_resume_verified']


def test_whole_local_proof_namespace_loss(case):
    c=case
    local=Path(c.contract['shared_storage']['local_temp_paths'][0]);local.mkdir(parents=True)
    p=local/'training_contract.json';p.write_text('Synthetic input proof')
    backup=c.review_path.parent/'backup.json';shutil.copyfile(p,backup)
    c.report['evidence'][str(p)]=sha256_file(p)
    c.write(c.root/'validation.json',c.report)
    c.revision['local_proof_backups']={str(p):dict(backup=str(backup),sha256=sha256_file(p))}
    rebind(c);shutil.rmtree(local);shutil.rmtree(c.parent)
    assert review(c)['original_local_evidence_read'] is False
    backup.write_text('tampered')
    with pytest.raises(ValueError,match='evidence changed'):review(c)


def test_no_fallback_to_parent_when_child_missing(case):
    c=case;shutil.rmtree(c.child)
    assert c.parent.exists()
    with pytest.raises(FileNotFoundError):review(c)


def test_child_payload_and_certificate_mutation(case):
    c=case;(c.child/'__0_0.distcp').write_bytes(b'bad')
    with pytest.raises(ValueError,match='size differs|SHA differs'):review(c)
    c.cert.write_text(c.cert.read_text()+' ')
    with pytest.raises(ValueError,match='certificate changed'):review(c)


def test_local_checkpoint_not_nfs_resume_candidate(case):
    c=case
    with tempfile.TemporaryDirectory() as tmp:
        local=Path(tmp)/c.child.name;shutil.copytree(c.child,local)
        c.revision['child']['checkpoint']=str(local);c.write(c.review_path,c.revision)
        with pytest.raises(ValueError,match='exact NFS'):review(c)


def test_false_gpu_evidence_still_rejected(case):
    c=case;c.report['next_update_exact']=False;c.write(c.root/'validation.json',c.report);rebind(c)
    with pytest.raises(ValueError,match='validation incomplete'):review(c)


def test_changed_config_or_production_adoption_is_rejected(case):
    c=case;changed=deepcopy(c.config);changed['max_steps']=35000
    with pytest.raises(ValueError):review_handoff(c.root,c.contract,changed,c.review_path)
    c.revision['production_adoption_authorized']=True;c.write(c.review_path,c.revision)
    with pytest.raises(ValueError,match='source binding'):review(c)


def test_shared_space_failure_is_not_bypassed(case, monkeypatch):
    def reject(*args):raise RuntimeError('Shared storage unavailable')
    monkeypatch.setattr('train.independent_checkpoint_handoff.enforce_shared_space',reject)
    with pytest.raises(RuntimeError,match='storage unavailable'):review(case)
