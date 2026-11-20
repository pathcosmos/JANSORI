"""Guard tests using the real prefix fixture; synthetic checkpoint contents."""
import json
import shutil

import pytest

from test_independent_checkpoint_handoff import case
from train.final_resume_probe import file_stats
from train.independent_child_resume import IndependentChildResume


def adapter(c):
    return IndependentChildResume(original_contract=c.original, parent_checkpoint=c.parent,
        destination=c.root/'training',parent_metadata=c.meta,child_contract=c.candidate,
        parent_proof=c.proof,verified_parent_stats=c.receipt['parent_stats'],execution_binding=c.binding,
        child_checkpoint=c.child,child_proof=json.loads(c.cert.read_text()),verified_child_stats=file_stats(c.child))


def test_select_validate_load_without_parent(case):
    c=case;a=adapter(c);shutil.rmtree(c.parent)
    assert a.checkpoints(c.root/'training')==[c.child]
    assert a.validate(json.loads((c.child/'meta.json').read_text()),c.candidate)['mode']=='exact_child_resume'
    assert a.load(lambda **kw:(kw['checkpoint_id']/'__0_0.distcp').read_bytes(),checkpoint_id=c.child).startswith(b'synthetic')
    assert not a.parent_selected


def test_missing_child_never_falls_back_to_existing_parent(case):
    c=case;a=adapter(c);shutil.rmtree(c.child)
    assert c.parent.exists()
    with pytest.raises(ValueError,match='fallback is disabled'):a.checkpoints(c.root/'training')


def test_unadmitted_newer_complete_child_is_rejected(case):
    c=case;a=adapter(c);shutil.copytree(c.child,c.root/'training/ckpt_0010500')
    with pytest.raises(ValueError,match='Latest completed child'):a.checkpoints(c.root/'training')


def test_load_scope_and_mutation_are_rejected(case):
    c=case;a=adapter(c)
    with pytest.raises(ValueError,match='exact admitted'):a.load(lambda **kw:None,checkpoint_id=c.child)
    a.checkpoints(c.root/'training')
    with pytest.raises(ValueError,match='exact admitted'):a.load(lambda **kw:None,checkpoint_id=c.parent)
    def mutate(**kw):(c.child/'rng_rank0.pt').write_bytes(b'bad')
    with pytest.raises(ValueError,match='changed'):a.load(mutate,checkpoint_id=c.child)


def test_load_fails_when_local_only_copy_remains(case,tmp_path):
    c=case;a=adapter(c);a.checkpoints(c.root/'training')
    local=tmp_path/c.child.name;shutil.copytree(c.child,local);shutil.rmtree(c.child)
    with pytest.raises(ValueError,match='exact admitted'):a.load(lambda **kw:None,checkpoint_id=local)
    with pytest.raises((ValueError,FileNotFoundError)):a.load(lambda **kw:None,checkpoint_id=c.child)
