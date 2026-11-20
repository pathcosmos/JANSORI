"""Real9500 prefix + synthetic suffix to57000; no GPU acceptance claimed."""
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile

import pytest

from common.data_contracts import artifact_path, identity, sha256_file, resolve
from scripts import completed_source_archive as archive
from scripts.tiered_checkpoint_retention import atomic_json


@pytest.fixture
def completed(tmp_path, monkeypatch):
    if not os.environ.get('DENSE_EXTENSION_LINEAGE_FIXTURE'):
        pytest.skip('Explicit immutable prefix fixture required')
    from test_restored_training_audit import RestoredAuditTests
    from scripts.audit_restored_training import audit_run
    from train.train import lr_at
    fixture = RestoredAuditTests()
    fixture.setUp()
    f = fixture.f
    metadata_temp = tempfile.TemporaryDirectory(prefix='completed-archive-fixture-', dir=resolve('runs'))
    fixture.addCleanup(metadata_temp.cleanup)
    scope = Path(metadata_temp.name)
    # Fixture owns only tiny synthetic payloads, not another1TiB temp pool.
    # Resource admission has separate tests; do not double-reserve live work.
    monkeypatch.setattr('train.extended_handoff.enforce_shared_space', lambda *args, **kwargs: {'fixture_only': True})
    try:
        f.contract['shared_storage']['nfs_pools']['transition'] = dict(paths=[str(scope)], budget_bytes=2**30)
        f.write(f.root / 'contract.json', f.contract)
        source = f.candidate['source_order'][0]
        generation = [dict(id=p['id'], prompt=p['text'], category=p['category'], generated_ids=[1]) for p in f.prompts]
        generations = [dict(step=s, sha256=identity(generation)) for s in f.contract['generation_steps'] if s > 9500]
        for rank in range(8):
            f.write(f.root / 'ledgers' / f'fixture-rank{rank}.json', dict(
                rank=rank, success=True, contract_id=f.candidate['id'], optimizer_initializations=1,
                steps=[dict(step=s, source_sequences={source: 64}) for s in range(9501, 57001)], generations=generations))
        rows = [dict(step=s, loss=2., gnorm=.2, lr=lr_at(s-1, f.config), tok_s=140000., tokens_b=s*2097152/1e9)
                for s in range(9510, 57001, 10)]
        for step in range(9750, 57001, 250):
            by_source = {g['source']: dict(valid_tokens=g['count']*4096, nll_sum=g['count']*4096*2., holdout_loss=2.)
                         for g in f.probe['groups']}
            categories = Counter()
            for g in f.probe['groups']:
                categories[g['category']] += g['count']*4096
            row = dict(step=step, contract_id=f.candidate['id'], valid_tokens=f.probe['target_tokens'],
                       nll_sum=f.probe['target_tokens']*2., holdout_loss=2., by_source=by_source,
                       by_category={k: dict(valid_tokens=n, nll_sum=n*2., holdout_loss=2.) for k, n in categories.items()})
            if step in f.contract['generation_steps']:
                row['fixed_generations'] = generation
            rows.append(row)
        (f.root / 'training/log.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in sorted(rows, key=lambda row: row['step'])))
        cp = fixture.cp.with_name('ckpt_0057000')
        fixture.cp.rename(cp)
        meta = json.loads((cp / 'meta.json').read_text()); meta['step'] = 57000
        f.write(cp / 'meta.json', meta)
        files = [dict(path=p.name, bytes=p.stat().st_size, sha256=sha256_file(p)) for p in sorted(cp.iterdir())]
        f.write(f.root / 'checkpoint_certificates/ckpt_0057000.json', dict(
            status='passed_main_checkpoint_storage', checkpoint=artifact_path(cp), step=57000,
            training_contract_id=f.candidate['id'], files=files, bytes=sum(row['bytes'] for row in files)))
        f.write(f.root / 'report.json', dict(status='main_target_completed_review_pending', final_checkpoint=artifact_path(cp), trained_tokens=119537664000))
        f.write(f.root / '.done', dict(report_sha256=sha256_file(f.root / 'report.json'), contract_sha256=sha256_file(f.root / 'contract.json')))
        audit = audit_run(f.root, 57000)
        # Only resource/process entry is synthetic; real new replay and all
        # historical lineage/step/source/generation/marker checks run below.
        monkeypatch.setattr('scripts.completed_training_storage.admit_completed', lambda _: dict(contract=f.contract, final_audit=audit))
        monkeypatch.setattr('scripts.completed_training_storage.active_source_processes', lambda _: [])
        yield f, audit, scope
    finally:
        fixture.doCleanups()


def test_complete_archive_recomputes_without_source_or_parent_payload_and_rejects_tampering(completed, monkeypatch):
    f, audit, scope = completed
    out = scope / 'archive'
    first = archive.freeze('synthetic_admission', out)
    digest = sha256_file(out / 'manifest.json')
    assert first['counts'] == audit['counts']
    assert first['counts']['committed_step_rank_pairs'] == 456000
    assert first['counts']['committed_tokens'] == 119537664000
    assert not first['actual8gpu_independence_verified'] and not first['parent_retirement_authorized']
    # Rename all fixture source files out of the way, including parent and
    # own checkpoint. Archived replay must still work without their presence.
    hidden = f.root.with_name(f.root.name + '-hidden')
    f.root.rename(hidden)
    try:
        assert archive.replay(out, digest) == first
        manifest_raw = (out / 'manifest.json').read_bytes()
        manifest = json.loads(manifest_raw)
        for fault in ['blob', 'missing_rank', 'path_escape', 'forged_counts', 'rng', 'completion', 'extra_blob', 'extra_root', 'pin']:
            changed = deepcopy(manifest)
            restore = {}
            if fault in ('blob', 'rng', 'completion', 'forged_counts'):
                if fault == 'blob': key = next(iter(changed['files']))
                elif fault == 'rng': key = next(k for k in changed['files'] if k.endswith('/rng_rank7.pt'))
                elif fault == 'completion': key = next(k for k in changed['files'] if k.endswith('/.done'))
                else: key = changed['audit']
                row = changed['files'][key]; dest = out / 'blobs' / row['blob']; restore[dest] = dest.read_bytes()
                if fault == 'forged_counts':
                    value = json.loads(restore[dest]); value['counts']['committed_tokens'] += 2097152
                    atomic_json(dest, value)
                    # Rebind both public and archived audit copies so rejection
                    # must come from actual ledger recomputation, not a stale SHA.
                    public = out / 'original_completed_audit.json'
                    restore[public] = public.read_bytes()
                    public.write_bytes(dest.read_bytes())
                elif fault == 'completion':
                    value = json.loads(restore[dest]); value['report_sha256'] = '0'*64
                    atomic_json(dest, value)
                else: dest.write_bytes(b'X' * len(restore[dest]))
                if fault != 'blob': row.update(bytes=dest.stat().st_size, sha256=sha256_file(dest))
            elif fault == 'missing_rank':
                del changed['files'][next(k for k in changed['files'] if '/ledgers/' in k)]
            elif fault == 'path_escape': next(iter(changed['files'].values()))['blob'] = '../manifest.json'
            elif fault == 'extra_blob': (out / 'blobs/unowned').write_text('unexpected')
            elif fault == 'extra_root': (out / 'unowned').write_text('unexpected')
            elif fault == 'pin': changed['unbound_change'] = True
            atomic_json(out / 'manifest.json', changed)
            try:
                with pytest.raises((ValueError, KeyError, json.JSONDecodeError),
                                   match='accounting/trace' if fault == 'forged_counts' else None):
                    archive.replay(out, digest if fault == 'pin' else sha256_file(out / 'manifest.json'))
            finally:
                for path, raw in restore.items(): path.write_bytes(raw)
                (out / 'blobs/unowned').unlink(missing_ok=True)
                (out / 'unowned').unlink(missing_ok=True)
                (out / 'manifest.json').write_bytes(manifest_raw)
        assert archive.replay(out, digest) == first
    finally:
        hidden.rename(f.root)


def test_live_source_existing_destination_and_capacity_fail_before_writes(completed, monkeypatch):
    _, _, scope = completed
    out = scope / 'archive'
    monkeypatch.setattr('scripts.completed_training_storage.active_source_processes', lambda _: [dict(pid=1)])
    with pytest.raises(RuntimeError, match='active'): archive.freeze('fixture', out)
    assert not out.exists()
    monkeypatch.setattr('scripts.completed_training_storage.active_source_processes', lambda _: [])
    monkeypatch.setattr(archive, 'size_tree', lambda _: 2**30)
    with pytest.raises(ValueError, match='capacity'): archive.freeze('fixture', out)
    assert not out.exists()
    monkeypatch.setattr(archive, 'size_tree', lambda _: 0)
    out.mkdir(); (out / 'keep').write_text('preserve')
    with pytest.raises(ValueError, match='Preserve'): archive.freeze('fixture', out)
    assert (out / 'keep').read_text() == 'preserve'
    failed = scope / 'failed-publication'
    def fail_replay(*args): raise RuntimeError('injected publication failure')
    monkeypatch.setattr(archive, 'replay', fail_replay)
    with pytest.raises(RuntimeError, match='publication failure'):
        archive.freeze('fixture', failed)
    assert (failed / 'manifest.json').exists() and not (failed / 'verified.json').exists()
    with pytest.raises(ValueError, match='Preserve'): archive.freeze('fixture', failed)


def test_incomplete_training_admission_creates_nothing(tmp_path, monkeypatch):
    def reject(_): raise ValueError('57000 not complete')
    monkeypatch.setattr('scripts.completed_training_storage.admit_completed', reject)
    out = tmp_path / 'archive'
    with pytest.raises(ValueError, match='not complete'): archive.freeze('fixture', out)
    assert not out.exists()


def test_conflicting_input_aliases_rejected(tmp_path):
    path = tmp_path / 'input'
    with pytest.raises(ValueError, match='Conflicting'):
        archive.merged_bindings({str(path): 'a'*64}, {str(path.parent / '.' / path.name): 'b'*64})
    assert archive.merged_bindings({str(path): 'a'*64}, {str(path): 'a'*64}) == {str(path): 'a'*64}
