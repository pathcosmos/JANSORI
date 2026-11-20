"""Final source verifier gates; all checkpoint tensors below are tiny fixtures."""
import json
from pathlib import Path
import pytest
from common.data_contracts import sha256_file
import scripts.inspect_budget_completed_source as module


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    main=tmp_path/'run';cp=main/'training/ckpt_0050000';cp.mkdir(parents=True)
    cert=main/'checkpoint_certificates/ckpt_0050000.json';cert.parent.mkdir()
    (cp/'meta.json').write_text(json.dumps({'step':50000,'training_contract':{'id':'id'}}))
    for name in ['.complete','.metadata','state.distcp',*[f'rng_rank{i}.pt' for i in range(8)]]:
        (cp/name).write_bytes(name.encode())
    files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in cp.iterdir()]
    cert.write_text(json.dumps(dict(step=50000,training_contract_id='id',checkpoint=str(cp),
        files=files,bytes=sum(x['bytes'] for x in files))))
    ledger=main/'ledger.json';ledger.write_text('{}')
    ident={'training_contract_id':'id','final_metadata_verified':True}
    result={'counts':{'committed_tokens':104857600000,'committed_step_rank_pairs':400000,
             'source_tokens':{'ko':104857600000}},'evidence':{str(ledger):sha256_file(ledger)}}
    monkeypatch.setattr(module,'inspect_identity',lambda *a,**kw:ident.copy())
    monkeypatch.setattr(module,'audit_completed',lambda *a:result)
    monkeypatch.setattr(module,'active_source_processes',lambda *a:[])
    return main,cp,cert,ledger


def test_full_own_file_hash_check_without_independence_claim(fixture):
    main,cp,cert,ledger=fixture
    result=module.inspect_completed_source(main,workers=2)
    assert result['full_payload_sha_verified'] and result['source_process_absence_observed']
    assert not any(result[k] for k in ['future_source_launch_locked','access_isolation_verified',
        'actual8gpu_verified','retirement_authorized','goal_complete'])


def test_same_size_payload_corruption_rejected(fixture):
    main,cp,cert,ledger=fixture;p=cp/'state.distcp';p.write_bytes(b'X'*p.stat().st_size)
    with pytest.raises(ValueError,match='SHA differs'):module.inspect_completed_source(main)


def test_source_live_and_restart_during_check_rejected(fixture,monkeypatch):
    main,*_=fixture
    monkeypatch.setattr(module,'active_source_processes',lambda *a:[{'pid':123}])
    with pytest.raises(ValueError,match='must be stopped'):module.inspect_completed_source(main)
    seen=iter([[],[{'pid':123}]])
    monkeypatch.setattr(module,'active_source_processes',lambda *a:next(seen))
    with pytest.raises(ValueError,match='absence changed'):module.inspect_completed_source(main)


def test_ledger_change_during_hash_rejected(fixture,monkeypatch):
    main,cp,cert,ledger=fixture;real=module.inventory
    def mutate(*args,**kwargs):
        result=real(*args,**kwargs);ledger.write_text('{"changed":true}');return result
    monkeypatch.setattr(module,'inventory',mutate)
    with pytest.raises(ValueError,match='ledger evidence changed'):module.inspect_completed_source(main)


@pytest.mark.parametrize('workers',[0,17,True])
def test_unbounded_or_boolean_worker_count_rejected(fixture,workers):
    with pytest.raises(ValueError,match='Hash workers'):module.inspect_completed_source(fixture[0],workers=workers)
