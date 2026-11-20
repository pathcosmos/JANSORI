"""Real sealed prefix and file hooks; never launch production or CUDA."""
from copy import deepcopy
import json
import shutil
import pytest
from test_independent_checkpoint_handoff import case
from train.independent_resume_bridge import PreparedIndependentAdmission
from train import monitored_extended_horizon as worker


def prepare(c):
    c.write(c.root/'training/training_contract.json',c.candidate)
    return PreparedIndependentAdmission(c.root,c.review_path)


def test_original_worker_identity_and_exact_own_selection(case):
    c=case;shutil.rmtree(c.parent);old_worker=worker.worker;old_handoff=worker.load_handoff;old_resume=worker.parent_resume
    a=prepare(c);original=json.loads((c.root/'training/training_contract.json').read_text())
    with a.diagnostic_hooks() as module:
        assert module.worker is old_worker
        receipt,candidate,proof=module.load_handoff(c.root,c.contract,c.config)
        adapter=module.parent_resume(receipt,candidate,proof,c.config,c.contract)
        assert candidate==original and adapter.checkpoints(c.root/'training')==[c.child]
        meta=json.loads((c.child/'meta.json').read_text());adapter.validate(meta,candidate)
        assert adapter.load(lambda **kw:(kw['checkpoint_id']/'__0_0.distcp').read_bytes(),checkpoint_id=c.child).startswith(b'synthetic')
    assert worker.load_handoff is old_handoff and worker.parent_resume is old_resume
    e=a.evidence();assert not e['actual8gpu_resume'] and not e['production_adoption_authorized']


def test_exception_restores_only_admission_hooks(case):
    a=prepare(case);old=(worker.worker,worker.load_handoff,worker.parent_resume)
    with pytest.raises(RuntimeError,match='diagnostic failure'):
        with a.diagnostic_hooks():raise RuntimeError('diagnostic failure')
    assert (worker.worker,worker.load_handoff,worker.parent_resume)==old


@pytest.mark.parametrize('fault',['checkpoint','source','config','candidate','nested'])
def test_changed_inputs_or_conflicting_hooks_are_rejected(case,fault):
    c=case;a=prepare(c)
    if fault=='checkpoint':(c.child/'rng_rank0.pt').write_bytes(b'changed')
    if fault=='source':c.write(c.root/'contract.json',dict(c.contract,unexpected=True))
    if fault in ['checkpoint','source']:
        with pytest.raises(ValueError,match='changed'):a.check()
    elif fault=='config':
        with pytest.raises(ValueError):a.handoff(c.root,c.contract,dict(c.config,max_steps=35000))
    elif fault=='candidate':
        with pytest.raises(ValueError):a.resume(a.receipt,dict(a.candidate,id='wrong'),a.proof,a.config,a.contract)
    else:
        with a.diagnostic_hooks():
            with pytest.raises(ValueError,match='different admission'):
                with a.diagnostic_hooks():pass
