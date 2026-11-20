import ast
from copy import deepcopy
from pathlib import Path
import unittest

from common.data_contracts import identity
from train.extended_horizon_transition import build_extension_candidate, validate_extension


def seal(d):
    d = deepcopy(d); d.pop('id', None); d['id'] = identity(d); return d


class ExtensionTest(unittest.TestCase):
    def setUp(self):
        cfg = dict(max_steps=57000, lr=.0003, warmup=1000, decay_frac=.15,
                   min_lr_ratio=.1, global_tokens=2097152, sampler_version=2)
        self.original = seal(dict(config=cfg, world_size=8, files={'a':'sha'},
            mix={'sha':'mix'}, source_order=['a'], holdout=['holdout'],
            code={'worker':'sha'}, execution={'deterministic':True},
            monitored_evaluation=dict(probe_sha256='p', prompts_sha256='g',
                code_sha256='w', generation_steps=[250,2500,5000,*range(10000,55001,5000),57000])))
        p = deepcopy(self.original);p['config']['max_steps']=35000
        p['monitored_evaluation']['generation_steps']=[250,2500,5000,*range(10000,35001,5000)]
        p['horizon_transition']={'parent_contract_id':self.original['id'],'parent_step':8000,
                                 'parent_metadata_id':'preserve me'}
        p=seal(p)
        self.meta=dict(training_contract=p,config=deepcopy(p['config']),step=10000,
                       world_size=8,sampler_version=2,tokens_per_step=2097152)

    def test_preserves_ancestry_without_authorizing_execution(self):
        before=deepcopy(self.meta);c,r=build_extension_candidate(self.meta,self.original)
        self.assertEqual(self.meta,before)
        self.assertEqual(c['horizon_transition'],before['training_contract']['horizon_transition'])
        self.assertEqual(r['total_tokens'],119537664000)
        for k in ['runtime_applied','checkpoint_payload_verified','distributed_resume_verified','lineage_sealed']:
            self.assertFalse(r[k])

    def test_actual_lr_prefix_and_restored_schedule(self):
        tree=ast.parse(Path('train/train.py').read_text())
        fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='lr_at')
        ns={};exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-lr','exec'),ns)
        lr=ns['lr_at'];c,_=build_extension_candidate(self.meta,self.original)
        self.assertTrue(all(lr(i,self.meta['config'])==lr(i,c['config']) for i in range(29750)))
        self.assertTrue(all(lr(i,c['config'])==lr(i,self.original['config']) for i in range(57001)))
        self.assertEqual(lr(48450,c['config']),.0003)
        self.assertAlmostEqual(lr(57000,c['config']),.00003)

    def test_rejects_unreviewed_changes_even_with_valid_digest(self):
        c,_=build_extension_candidate(self.meta,self.original)
        cases=[(['config','lr'],.0002),(['files','a'],'changed'),
               (['horizon_transition','parent_metadata_id'],'changed'),
               (['code','worker'],'changed'),(['execution','deterministic'],False),
               (['monitored_evaluation','generation_steps'],[10000,57000]),
               (['monitored_evaluation','probe_sha256'],'changed'),(['world_size'],4)]
        for path,value in cases:
            with self.subTest(path=path):
                bad=deepcopy(c);d=bad
                for k in path[:-1]:d=d[k]
                d[path[-1]]=value
                with self.assertRaises(ValueError):
                    validate_extension(self.meta['training_contract'],seal(bad),self.original,10000)

    def test_rejects_bad_metadata_and_changed_decay_prefix(self):
        for key,value in [('step',True),('step',8000),('step',29751),('step',35000),
                          ('world_size',4),('tokens_per_step',1),('sampler_version',1)]:
            with self.subTest(key=key,value=value):
                m=deepcopy(self.meta);m[key]=value
                with self.assertRaises(ValueError):build_extension_candidate(m,self.original)
        m=deepcopy(self.meta);m['step']=29750
        build_extension_candidate(m,self.original)
        m=deepcopy(self.meta);m['training_contract']['id']='wrong'
        with self.assertRaises(ValueError):build_extension_candidate(m,self.original)
        m=deepcopy(self.meta);m['config']['lr']=.1
        with self.assertRaises(ValueError):build_extension_candidate(m,self.original)


if __name__=='__main__':unittest.main()
