"""Actual NFS file protocol/lineage; synthetic checkpoints, not GPU proof."""
import json
from pathlib import Path
import shutil
import tempfile

import pytest
from common.data_contracts import resolve,sha256_file
from scripts import prepare_latest_independent_revision as module
from test_independent_checkpoint_handoff import case,rebind
from train.independent_operational_admission import PreparedOperationalAdmission


def initialize(c,monkeypatch):
    tmp=tempfile.TemporaryDirectory(dir=resolve('runs'),prefix='revision-pool-');c.addCleanup(tmp.cleanup)
    pool=Path(tmp.name)
    c.contract['shared_storage']['nfs_pools']['transition']=dict(paths=[str(pool)],budget_bytes=2**30)
    c.write(c.root/'training/training_contract.json',c.candidate);rebind(c)
    monkeypatch.setattr('scripts.extended_horizon_storage.enforce_shared_space',lambda *a:{'fixture':True})
    return pool


def add_checkpoint(c,step):
    cp=c.child.with_name(f'ckpt_{step:07d}');shutil.copytree(c.child,cp)
    meta=json.loads((cp/'meta.json').read_text());meta['step']=step;c.write(cp/'meta.json',meta)
    proof=json.loads(c.cert.read_text());proof.update(checkpoint=str(cp),step=step)
    proof['files']=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(cp.iterdir())]
    proof['bytes']=sum(r['bytes'] for r in proof['files'])
    cert=c.cert.with_name(cp.name+'.json');c.write(cert,proof)
    return cp,cert


def test_latest_refresh_with_rotated_template_child_and_local_proof_loss(case,monkeypatch):
    c=case;pool=initialize(c,monkeypatch)
    local=Path(c.contract['shared_storage']['local_temp_paths'][0]);local.mkdir(parents=True)
    proof=local/'training_contract.json';proof.write_text('small immutable validation proof')
    backup=c.review_path.parent/'backup.json';shutil.copyfile(proof,backup)
    c.report['evidence'][str(proof)]=sha256_file(proof);c.write(c.root/'validation.json',c.report)
    c.revision['local_proof_backups']={str(proof):dict(backup=str(backup),sha256=sha256_file(proof))};rebind(c)
    newer,_=add_checkpoint(c,10500);shutil.rmtree(c.child);shutil.rmtree(c.parent);shutil.rmtree(local)
    template_sha=sha256_file(c.review_path);contract_sha=sha256_file(c.root/'contract.json')
    result=module.prepare(c.root,c.review_path,pool/'attempt',template_sha256=template_sha)
    assert result['selection']['checkpoint']==str(newer) and result['selection']['step']==10500
    assert result['review']['checkpoint']['full_payload_sha_refreshed']
    assert not result['production_adopted'] and not result['actual8gpu_verified']
    assert sha256_file(c.review_path)==template_sha and sha256_file(c.root/'contract.json')==contract_sha
    output=pool/'attempt';rev=json.loads((output/'revision.json').read_text())
    copied=Path(rev['local_proof_backups'][str(proof)]['backup'])
    assert copied.parent==output/'proof_backups' and sha256_file(copied)==sha256_file(backup)
    admitted=PreparedOperationalAdmission(c.root,output/'revision.json',rank=7)
    assert admitted.child==newer and admitted.candidate==c.candidate
    with pytest.raises(ValueError,match='Preserve'):module.prepare(c.root,c.review_path,output,template_sha256=template_sha)


@pytest.mark.parametrize('fault',['uncertified','incomplete','unknown_name','symlink','wrong_identity','rank_missing','missing_all'])
def test_latest_invalid_never_falls_back_to_older_or_parent(case,monkeypatch,fault):
    c=case;initialize(c,monkeypatch);newer,cert=add_checkpoint(c,10500)
    if fault=='uncertified':cert.unlink()
    elif fault=='incomplete':(newer/'.complete').unlink()
    elif fault=='unknown_name':newer.rename(newer.with_name('ckpt_0010500.partial'))
    elif fault=='symlink':
        other=newer.with_name('other');newer.rename(other);newer.symlink_to(other,target_is_directory=True)
    elif fault=='wrong_identity':
        p=json.loads(cert.read_text());p['training_contract_id']='wrong';c.write(cert,p)
    elif fault=='rank_missing':(newer/'__7_0.distcp').unlink()
    elif fault=='missing_all':shutil.rmtree(c.child);shutil.rmtree(newer)
    with pytest.raises((ValueError,FileNotFoundError)):module.select_latest(c.root)
    assert c.parent.exists()


@pytest.mark.parametrize('fault',['payload','template','out_of_scope','backup','copy_failure','advanced_during_review'])
def test_no_prepared_marker_on_invalid_input_or_interruption(case,monkeypatch,fault):
    c=case;pool=initialize(c,monkeypatch);digest=sha256_file(c.review_path);output=pool/'attempt'
    if fault=='payload':
        p=c.child/'__0_0.distcp';p.write_bytes(b'x'*p.stat().st_size)
    elif fault=='template':digest='0'*64
    elif fault=='out_of_scope':output=c.root/'wrong_scope'
    elif fault=='backup':
        backup=c.review_path.parent/'backup.json';backup.write_text('bad')
        c.revision['local_proof_backups']={'not-allowed':dict(backup=str(backup),sha256='0'*64)}
        c.write(c.review_path,c.revision);digest=sha256_file(c.review_path)
    elif fault=='copy_failure':
        original=module.atomic_json
        def write(path,data):
            if path.name=='revision.json':raise OSError('injected publication failure')
            return original(path,data)
        monkeypatch.setattr(module,'atomic_json',write)
    elif fault=='advanced_during_review':
        original=module.PreparedOperationalAdmission
        def admission(*args,**kw):
            a=original(*args,**kw);add_checkpoint(c,10500);return a
        monkeypatch.setattr(module,'PreparedOperationalAdmission',admission)
    with pytest.raises((ValueError,OSError)):module.prepare(c.root,c.review_path,output,template_sha256=digest)
    assert not (output/'prepared.json').exists()
    if fault in ('payload','copy_failure','advanced_during_review'):
        assert (output/'intent.json').is_file()
        with pytest.raises(ValueError,match='Preserve'):module.prepare(c.root,c.review_path,output,template_sha256=digest)
