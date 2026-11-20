import hashlib
import json
from pathlib import Path
import tempfile

import pytest

from common.data_contracts import sha256_file
from scripts import copy_reviewed_checkpoint_archive as archive


@pytest.fixture
def case(tmp_path):
    # Exercise a real cross-filesystem copy, including local atomic publication.
    with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='archive-test-') as work:
        local = Path(work)/'main'/'historical_checkpoint_archive_v1'
        source = tmp_path/'runs'/'old_diagnostic'/'ckpt_0000004'
        source.mkdir(parents=True)
        for name in ['.complete', '.metadata', *[f'rng_rank{i}.pt' for i in range(8)]]:
            (source/name).write_bytes(name.encode()*50)
        (source/'__0_0.distcp').write_bytes(b'payload' * 10000)
        (source/'meta.json').write_text(json.dumps(dict(step=4, training_contract=dict(id='original-id'))))
        size = sum(p.stat().st_size for p in source.iterdir())
        main = tmp_path/'runs'/'main'
        main.mkdir()
        contract = dict(shared_storage=dict(local_temp_paths=[str(local.parent)],
                                            temporary_budget_bytes=1024*archive.GIB))
        (main/'contract.json').write_text(json.dumps(contract))
        review = tmp_path/'review.json'
        review.write_text(json.dumps(dict(other_checkpoints=[dict(path=str(source.relative_to(tmp_path)),bytes=size)])))
        plan = dict(purpose='historical_checkpoint_copy_only_v1', delete_source=False,
                    project_root=str(tmp_path), main_run=str(main), source=str(source),
                    local_archive_root=str(local), receipt_root=str(tmp_path/'runs/3b_57k_transition_v2/archive'),
                    review_path=str(review), review_sha256=sha256_file(review),
                    main_contract_sha256=sha256_file(main/'contract.json'), source_bytes=size,
                    code={}, future_temp_bytes=200*archive.GIB, copy_workers=2, hash_workers=2)
        path = tmp_path/'plan.json';path.write_text(json.dumps(plan))
        slot = hashlib.sha256(str(source).encode()).hexdigest()[:24]
        def admission(*args):
            return dict(local_temporary_used_bytes=archive.size_tree(local))
        yield dict(path=path, plan=plan, source=source, local=local, slot=slot,
                   evidence=Path(plan['receipt_root'])/slot, admission=admission, main=main)


def run(c, **kwargs):
    return archive.copy_one(c['path'], admission=c['admission'], **kwargs)


def test_copy_integrity_and_loss_recovery(case):
    c=case;before=archive.snapshot(c['source'])
    result=run(c)
    assert result['status']=='verified_copy_source_preserved'
    assert not result['nfs_resume_candidate'] and not result['deletion_authorized']
    assert archive.snapshot(c['source'])==before
    for file in c['source'].iterdir():
        assert file.read_bytes()==(c['local']/c['slot']/file.name).read_bytes()
    assert run(c)['timing']['revalidated_existing_destination']
    import shutil
    shutil.rmtree(c['local'])  # Simulate loss of the entire disposable disk namespace.
    assert run(c)['status']=='verified_copy_source_preserved'
    assert archive.snapshot(c['source'])==before


@pytest.mark.parametrize('point',['copied_file','copied_payload','verified_copy','published_local_before_ack'])
def test_interrupted_copy_and_publication_resume(case, point):
    c=case
    def fail(where):
        if where==point:raise InterruptedError(point)
    with pytest.raises(InterruptedError):run(c,fault=fail)
    assert not (c['evidence']/'receipt.json').exists()
    assert c['source'].is_dir()
    assert run(c)['status']=='verified_copy_source_preserved'
    assert (c['evidence']/'receipt.json').exists()
    assert not (c['local']/(c['slot']+'.partial')).exists()


def test_source_change_never_acknowledged(case):
    c=case
    def mutate(where):
        if where=='copied_payload':(c['source']/'rng_rank0.pt').write_bytes(b'changed')
    with pytest.raises(ValueError,match='Source changed'):run(c,fault=mutate)
    assert not (c['evidence']/'receipt.json').exists()
    with pytest.raises(ValueError,match='Source changed'):run(c)


def test_extra_partial_residue_preserved(case):
    c=case
    def fail(where):
        if where=='copied_payload':raise InterruptedError()
    with pytest.raises(InterruptedError):run(c,fault=fail)
    residue=c['local']/(c['slot']+'.partial')/'unknown'
    residue.write_bytes(b'do not remove')
    with pytest.raises(ValueError,match='residue|leftovers'):run(c)
    assert residue.read_bytes()==b'do not remove'


def test_unowned_destination_rejected(case):
    c=case;(c['local']/c['slot']).mkdir(parents=True)
    with pytest.raises(ValueError,match='Unowned'):run(c)


def test_real_process_death_after_rename(case):
    import subprocess
    import sys
    c=case
    code = '''
import os, signal, sys
from pathlib import Path
from scripts import copy_reviewed_checkpoint_archive as a
def die(point):
    if point == 'published_local_before_ack':os.kill(os.getpid(),signal.SIGKILL)
a.copy_one(Path(sys.argv[1]),fault=die,
           admission=lambda *args:dict(local_temporary_used_bytes=a.size_tree(Path(sys.argv[2]))))
'''
    result=subprocess.run([sys.executable,'-c',code,str(c['path']),str(c['local'])],timeout=20)
    assert result.returncode == -9
    assert (c['local']/c['slot']).is_dir()
    assert not (c['evidence']/'receipt.json').exists()
    assert run(c)['timing']['revalidated_existing_destination']


def test_storage_failure_before_any_outputs(case):
    c=case
    def full(*args):raise RuntimeError('Shared local temporary/copy/reserve unavailable')
    with pytest.raises(RuntimeError,match='unavailable'):
        archive.copy_one(c['path'],admission=full)
    assert not c['evidence'].parent.exists() and not c['local'].exists()


def test_future_final_budget_and_live_contract_change(case):
    c=case
    def occupied(*args):return dict(local_temporary_used_bytes=900*archive.GIB)
    with pytest.raises(RuntimeError,match='peak exceeds'):
        archive.copy_one(c['path'],admission=occupied)
    assert not c['local'].exists()
    contract=c['main']/'contract.json';contract.write_text(contract.read_text()+' ')
    with pytest.raises(ValueError,match='Main contract changed'):run(c)


def test_incomplete_checkpoint_and_symlink_rejected(case):
    c=case;(c['source']/'rng_rank7.pt').unlink()
    with pytest.raises(ValueError,match='Incomplete'):run(c)
    (c['source']/'rng_rank7.pt').symlink_to(c['source']/'rng_rank6.pt')
    with pytest.raises(ValueError,match='file type'):run(c)
