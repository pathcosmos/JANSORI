"""Actual metadata files and fresh hashes, with synthetic training identities."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from common.data_contracts import sha256_file
from train.budget_horizon_contract import BRIDGE_CODE, bound_candidate
from scripts.inspect_budget_completed_identity import inspect, MetadataReader
from test_budget_completed_identity import documents, parent_metadata


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def on_disk(tmp_path, parent_metadata, *, final=False):
    c,cfg,r,p,a = documents(tmp_path, parent_metadata)
    main = Path(c['main_run'])
    write(main/'parent_certificate.json',p)
    binding = deepcopy(c['budget_execution'])
    binding['parent_certificate_sha256'] = sha256_file(main/'parent_certificate.json')
    binding['code'] = {n:sha256_file(Path(n)) for n in BRIDGE_CODE}
    a = bound_candidate(r['parent_metadata'], binding)
    c.update(budget_execution=binding, code=dict(binding['code']))
    r.update(execution_binding=deepcopy(binding), candidate_contract_id=a['id'])
    write(main/'main.json',cfg);write(main/'parent_handoff.json',r)
    write(main/'training/training_contract.json',a)
    c['configurations'] = {'main.json':sha256_file(main/'main.json')}
    c['evidence'] = {str(main/'parent_handoff.json'):sha256_file(main/'parent_handoff.json')}
    write(main/'contract.json',c)
    if final:
        checkpoint=main/'training/ckpt_0050000'
        meta=dict(step=50000,config=cfg,training_contract=a,world_size=8,
                  tokens_per_step=2097152,sampler_version=2)
        write(checkpoint/'meta.json',meta)
        cert=dict(deepcopy(p),step=50000,checkpoint=str(checkpoint),training_contract_id=a['id'])
        for row in cert['files']:
            if row['path']=='meta.json':
                row.update(bytes=(checkpoint/'meta.json').stat().st_size,sha256=sha256_file(checkpoint/'meta.json'))
        cert['bytes']=sum(r['bytes'] for r in cert['files'])
        write(main/'checkpoint_certificates/ckpt_0050000.json',cert)
        write(main/'report.json',dict(status='main_target_completed_review_pending',
            trained_tokens=104857600000,final_checkpoint=str(checkpoint)))
        write(main/'.done',dict(report_sha256=sha256_file(main/'report.json'),
            contract_sha256=sha256_file(main/'contract.json')))
    return main


def test_identity_collection_needs_no_parent_payloads(tmp_path,parent_metadata):
    main=on_disk(tmp_path,parent_metadata)
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result=inspect(main)
    assert result['training_contract_id']==json.loads((main/'training/training_contract.json').read_text())['id']
    assert not result['final_metadata_verified'] and not result['actual8gpu_verified']
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert not list(tmp_path.rglob('parent_checkpoint'))


def test_final_byte_metadata_does_not_claim_tensor_or_exit_verification(tmp_path,parent_metadata):
    main=on_disk(tmp_path,parent_metadata,final=True)
    result=inspect(main,final=True)
    assert result['final_metadata_verified']
    assert not any(result[k] for k in ['full_payload_sha_verified','source_exit_verified',
        'ledger_audit_verified','actual8gpu_verified','retirement_authorized','goal_complete'])
    assert not list(tmp_path.rglob('*.distcp'))


def test_final_option_requires_actual_final_files(tmp_path,parent_metadata):
    main=on_disk(tmp_path,parent_metadata)
    with pytest.raises(ValueError, match='Expected regular immutable file'): inspect(main,final=True)


@pytest.mark.parametrize('fault',['config','handoff','certificate','actual','code','final_meta','marker'])
def test_changed_pinned_inputs_rejected(tmp_path,parent_metadata,fault):
    main=on_disk(tmp_path,parent_metadata,final=True)
    names={'config':'main.json','handoff':'parent_handoff.json','certificate':'parent_certificate.json',
           'actual':'training/training_contract.json','final_meta':'training/ckpt_0050000/meta.json',
           'marker':'.done'}
    if fault=='code':
        p=main/'contract.json';v=json.loads(p.read_text());v['code']['train/budget_parent_resume.py']='e'*64;write(p,v)
    elif fault in ('actual','marker'):
        p=main/names[fault];v=json.loads(p.read_text());v['id' if fault=='actual' else 'report_sha256']='e'*64;write(p,v)
    else:
        with (main/names[fault]).open('a') as f:f.write(' ')
    with pytest.raises(ValueError): inspect(main,final=True)


def test_reader_rejects_payload_and_parent_metadata_access(tmp_path):
    reader=MetadataReader();reader.denied=[tmp_path/'parent_checkpoint']
    for path in [tmp_path/'parent_checkpoint/meta.json',tmp_path/'state.distcp']:
        with pytest.raises(ValueError):reader.read(path)


def test_reader_keeps_first_snapshot_and_rechecks_after_pin(tmp_path):
    from scripts.audit_checkpoint_retention import ChangedDuringAudit
    reader=MetadataReader();p=tmp_path/'contract.json';write(p,{'a':1})
    reader.read(p);digest=reader.digest(p)
    with pytest.raises(ValueError):reader.read(p)
    with p.open('a') as f:f.write(' ')
    reader.pin(p,digest)
    with pytest.raises(ChangedDuringAudit):reader.snapshot.unchanged()


def test_evidence_collision_cannot_hide_wrong_code_pin(tmp_path,parent_metadata):
    main=on_disk(tmp_path,parent_metadata)
    path=main/'contract.json';c=json.loads(path.read_text())
    name='scripts/report_resource_usage.py'
    c['code'][name]='e'*64
    c['evidence'][name]=sha256_file(Path(name))
    write(path,c)
    with pytest.raises(ValueError,match='SHA differs'):inspect(main)
