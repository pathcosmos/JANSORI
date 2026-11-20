from copy import deepcopy
from pathlib import Path

import pytest

from scripts.training_stage_storage_revision import revised_contract, DEFERRED, SCOPE, METADATA_ALLOWANCE


def fixture(tmp_path):
    main=tmp_path/'main'
    c=dict(purpose='main_training',target_steps=57000,main_run=str(main),
           configurations={'main.json':'unchanged'},code={'frozen.py':'unchanged'},
           tiered_retention=dict(nfs_keep=3,local_keep=15,adaptive_local_pruning=True,resume_from_local=False),
           shared_storage=dict(reserve_bytes=200*2**30,temporary_budget_bytes=1024*2**30,
               nfs_pools={name:dict(paths=[str(tmp_path/name)],budget_bytes=n*2**30)
                          for name,n in zip(DEFERRED,[8,1,1,100])}))
    c['shared_storage']['nfs_pools']['training']=dict(paths=[str(main)],budget_bytes=250*2**30)
    a=dict(authorized=True,scope=SCOPE,target_step=57000,resume_step=10500,
           deferred_pool_names=list(DEFERRED),user_decision='Proceed with current-stage storage guards')
    return c,a


def test_only_unused_future_reservations_change(tmp_path):
    c,a=fixture(tmp_path);before=deepcopy(c);d=revised_contract(c,a)
    assert c==before
    for k in ('code','configurations','tiered_retention'):assert d[k]==c[k]
    assert d['shared_storage']['nfs_pools']['training']==c['shared_storage']['nfs_pools']['training']
    assert d['shared_storage']['reserve_bytes']==200*2**30
    assert d['shared_storage']['temporary_budget_bytes']==1024*2**30
    assert sum(c['shared_storage']['nfs_pools'][n]['budget_bytes']-d['shared_storage']['nfs_pools'][n]['budget_bytes'] for n in DEFERRED)==110*2**30-4*METADATA_ALLOWANCE
    assert d['storage_phase_revision']['restore_before_final_stage']


@pytest.mark.parametrize('kind',['authorization','target','existing','overlap','safety','repeat'])
def test_rejects_unapproved_or_live_scope(tmp_path,kind):
    c,a=fixture(tmp_path)
    if kind=='authorization':a['authorized']=False
    elif kind=='target':a['target_step']=35000
    elif kind=='existing':Path(c['shared_storage']['nfs_pools']['hf_and_serving']['paths'][0]).mkdir()
    elif kind=='overlap':c['shared_storage']['nfs_pools']['hf_and_serving']['paths']=[c['main_run']]
    elif kind=='safety':c['shared_storage']['reserve_bytes']=199*2**30
    elif kind=='repeat':c=revised_contract(c,a)
    with pytest.raises(ValueError):revised_contract(c,a)
