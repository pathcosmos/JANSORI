"""Final accounting and immutable filesystem boundaries; no production writes."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from common.data_contracts import sha256_file
from scripts.audit_budget_completed_ledgers import LedgerReader, completed_counts, audit_completed
from test_budget_horizon import fixture, records
from test_budget_completed_identity import parent_metadata
from test_inspect_budget_completed_identity import on_disk


@pytest.fixture(scope='module')
def completed_args():
    args = fixture()
    args['through_step'] = 50000
    args['child_records'] = records(args['child'],16001,50000,'child',success=True)
    return args


def test_full_unique_accounting_excludes_replay_and_old_tails(completed_args):
    args = deepcopy(completed_args)
    args['original_records'] = records(args['original'],1,8001,'original')
    args['child_records'] += records(args['child'],50000,50000,'retry',success=True)
    result = completed_counts(**args)
    assert result['committed_tokens'] == 104857600000
    assert result['committed_step_rank_pairs'] == 400000
    assert result['successful_target_ranks'] == list(range(8))
    assert result['replayed_tokens_through_prefix'] == 2097152
    assert result['segments'][0]['persisted_attempt_tokens_beyond_prefix'] == 2097152
    assert not result['checkpoint_payload_verified']
    assert not result['distributed_resume_verified']


@pytest.mark.parametrize('fault',['rank','success','gap','identity','source','prefix','old_target'])
def test_final_accounting_rejects_incomplete_or_relabelled_work(completed_args,fault):
    args = deepcopy(completed_args)
    if fault=='rank': args['child_records'].pop()
    if fault=='success': args['child_records'][-1][1]['success']=False
    if fault=='gap': args['child_records'][0][1]['steps'].pop(0)
    if fault=='identity': args['child_records'][0][1]['contract_id']=args['restored']['id']
    if fault=='source': args['child_records'][0][1]['steps'][0]['source_sequences']={'wrong':64}
    if fault=='prefix': args['through_step']=16002
    if fault=='old_target': args['through_step']=57000
    with pytest.raises(ValueError): completed_counts(**args)


def test_reader_denies_ancestors_and_payload_even_without_named_owner(tmp_path):
    reader=LedgerReader([])
    for p in [tmp_path/'other/parent_checkpoint/meta.json',tmp_path/'state.distcp']:
        with pytest.raises(ValueError,match='forbidden'): reader.read(p)


def test_bind_then_read_preserves_first_snapshot(tmp_path):
    path=tmp_path/'record.json';path.write_text('{"rank": 0}')
    reader=LedgerReader([]);digest=sha256_file(path)
    reader.bind(path,digest)
    assert reader.read(path)=={'rank':0}
    with pytest.raises(ValueError,match='Repeated'): reader.read(path)
    path.write_text('{"rank": 1}')
    with pytest.raises(ValueError): reader.bind(path,sha256_file(path))
    with pytest.raises(ValueError): reader.snapshot.unchanged()


def test_change_after_bind_and_partial_jsonl_rejected(tmp_path):
    path=tmp_path/'record.json';path.write_text('{"rank": 0}')
    reader=LedgerReader([]);reader.bind(path,sha256_file(path))
    path.write_text('{"rank": 1}')
    with pytest.raises(ValueError,match='before reading'): reader.read(path)
    trace=tmp_path/'log.jsonl';trace.write_text('{"step": 50000}')
    with pytest.raises(ValueError,match='Incomplete trace'): reader.read(trace,lines=True)


def test_unfinished_run_refused_before_ledger_reads(tmp_path,parent_metadata,monkeypatch):
    main=on_disk(tmp_path,parent_metadata)
    def forbidden(*args,**kwargs): raise AssertionError('Accounting reached before final metadata')
    monkeypatch.setattr(LedgerReader,'read',forbidden)
    with pytest.raises(ValueError,match='Expected regular immutable file'): audit_completed(main)
