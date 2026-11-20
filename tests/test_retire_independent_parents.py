"""Actual tiny DCP NFS/local retirement; RuntimeView GPU acceptance is mocked."""
import json
from pathlib import Path
import shutil

import pytest
from common.data_contracts import sha256_file
from scripts.tiered_checkpoint_retention import atomic_json,size_tree
from scripts import retire_independent_parents as module
from scripts import independent_parent_retention as audit
from train.final_resume_probe import file_stats
from test_independent_parent_retention import retired


@pytest.fixture
def pending(retired,monkeypatch):
    c=retired;view=c.view
    # Rebuild source only inside the tiny disposable fixture; no real payloads.
    for entry in c.entries:
        source=Path(entry['source']);shutil.copytree(entry['destination'],source)
        p=source.parent.parent/'parent_handoff.json';receipt=json.loads(p.read_text());receipt['parent_stats']=file_stats(source);atomic_json(p,receipt)
        (source.parent/'.protect.lock').touch()
        for suffix in ('.transaction.json','.receipt.json'):atomic_json(source.with_name(source.name+suffix),{'fixture_metadata':True})
    shutil.rmtree(c.local);shutil.rmtree(view.control/'parent_retirement')
    view.contract['shared_storage'].update(nfs_pools={'transition':dict(paths=[str(view.control.parent)],budget_bytes=2**30)})
    atomic_json(view.main/'contract.json',view.contract);view.binding['main_contract_sha256']=sha256_file(view.main/'contract.json')
    c.plan=module.build_plan(view.main,view.control,authorized=True);atomic_json(c.plan_path,c.plan)
    c.entries=c.plan['entries'];view.binding['parent_retirement']['sha256']=sha256_file(c.plan_path)
    c.observed=dict(phase='running',completion=None,execution=str(c.execution),
        processes=dict(all8_consumed_live=True,supervisor={'fixture':True},launcher={'fixture':True}),
        consumption=dict(acknowledgement_sha256=sha256_file(c.execution.parent/'consumption/supervisor_verified.json')))
    view.observe=lambda:c.observed
    monkeypatch.setattr(module,'RuntimeView',lambda _:view)
    monkeypatch.setattr(module,'enforce_shared_space',lambda *a:dict(local_temporary_used_bytes=size_tree(c.local),nfs_free_bytes=1000*2**30,local_free_bytes=1000*2**30))
    return c


def test_actual_executor_archives_only_two_payloads_and_keeps_receipts(pending):
    c=pending;metadata={name:sha256_file(name) for name in c.plan['preserved_nfs_metadata']}
    result=module.execute(c.view.control)
    assert result['status']=='retired_two_protected_parents_verified_local_history'
    assert result['goal_complete'] is False
    assert all(not Path(e['source']).exists() and Path(e['destination']).is_dir() for e in c.entries)
    assert metadata=={name:sha256_file(name) for name in metadata}
    again=module.execute(c.view.control);assert again['status']=='verified_existing_parent_retirements'
    shutil.rmtree(c.local)
    lost=module.execute(c.view.control)
    assert all(r['local_namespace_missing'] and not r['nfs_resume_candidate'] for r in lost['parents'])


@pytest.mark.parametrize('fault',['acceptance','rank','consumption','completed','space','unknown_local','duplicate_local','unknown_nfs','metadata'])
def test_refuse_unaccepted_dead_or_out_of_budget_retirement(pending,monkeypatch,fault):
    c=pending;e=c.entries[0]
    if fault=='acceptance':
        def refuse(*a):raise ValueError('actual8GPU missing')
        monkeypatch.setattr(module,'RuntimeView',refuse)
    if fault=='rank':c.observed['processes']['all8_consumed_live']=False
    if fault=='consumption':c.observed['consumption']=None
    if fault=='completed':c.observed['completion']={'target_saved':True}
    if fault=='space':
        monkeypatch.setattr(module,'enforce_shared_space',lambda *a:dict(local_temporary_used_bytes=900*2**30,nfs_free_bytes=1000*2**30,local_free_bytes=1000*2**30))
    if fault=='unknown_local':(c.local/'unowned').mkdir(parents=True)
    if fault=='duplicate_local':
        Path(e['destination']).mkdir(parents=True);Path(e['destination']+'.partial').mkdir()
    if fault=='unknown_nfs':Path(e['source']).with_name('unrecognized_checkpoint').mkdir()
    if fault=='metadata':next(iter(c.plan['preserved_nfs_metadata'])) and Path(next(iter(c.plan['preserved_nfs_metadata']))).write_text('changed')
    with pytest.raises((ValueError,RuntimeError)):module.execute(c.view.control)
    assert all(Path(row['source']).exists() for row in c.entries)
    assert not (c.view.control/'parent_retirement/.done').exists()


@pytest.mark.parametrize('point',['copied_file','verified_copy','published_before_ack','renamed_source','retired_file'])
def test_copy_and_retirement_interruptions_preserve_and_resume(pending,point):
    c=pending;hit=[]
    def crash(where):
        if where==point and not hit:hit.append(where);raise RuntimeError('injected interruption')
    with pytest.raises(RuntimeError,match='injected'):module.execute(c.view.control,fault=crash)
    assert hit and not (c.view.control/'parent_retirement/.done').exists()
    assert all(Path(p).is_file() for p in c.plan['preserved_nfs_metadata'])
    result=module.execute(c.view.control)
    assert len(result['parents'])==2 and all(not Path(e['source']).exists() for e in c.entries)


def test_cancel_before_copy_preserves_both_sources(pending):
    c=pending
    with pytest.raises(InterruptedError):module.execute(c.view.control,cancel=lambda:True)
    assert all(Path(e['source']).exists() for e in c.entries)


def test_attempt_change_during_copy_stops_before_nfs_retirement(pending):
    c=pending
    def change(where):
        if where=='verified_copy':c.observed['execution']=str(c.execution.parent/'other.json')
    with pytest.raises(ValueError,match='attempt/consumption'):module.execute(c.view.control,fault=change)
    assert all(Path(e['source']).exists() for e in c.entries)


def test_original_metadata_change_or_unowned_residue_after_completion_fails_audit(pending):
    c=pending;module.execute(c.view.control)
    path=Path(next(iter(c.plan['preserved_nfs_metadata'])));raw=path.read_bytes();path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='metadata'):module.execute(c.view.control)
    path.write_bytes(raw)
    Path(c.entries[0]['source']).with_name('unexpected').mkdir()
    with pytest.raises(ValueError,match='unfinished/unrecognized'):module.execute(c.view.control)


def test_partial_optional_archive_loss_is_not_reported_as_verified(pending):
    c=pending;module.execute(c.view.control);shutil.rmtree(c.entries[0]['destination'])
    with pytest.raises(ValueError,match='Partial'):module.execute(c.view.control)
