import ast
from copy import deepcopy
from pathlib import Path
import unittest

from common.data_contracts import identity
from train.horizon_transition import build_candidate, validate_transition


def sealed(value):
    value = deepcopy(value)
    value.pop('id', None)
    value['id'] = identity(value)
    return value


class HorizonTransitionTest(unittest.TestCase):
    def setUp(self):
        cfg = dict(max_steps=57000, lr=3e-4, min_lr_ratio=.1, warmup=1000,
                   decay_frac=.15, global_tokens=2097152, sampler_version=2,
                   beta1=.9, beta2=.95, seed=1234)
        self.parent = sealed(dict(version=1, config=cfg, world_size=8,
            mix={'tokenizer_sha256': 'tokenizer'}, source_order=['ko', 'en', 'code', 'math'],
            files={'input': {'sha256': 'input', 'bytes': 4096}}, holdout=['validation'],
            code={'train/train.py': 'original'}, execution={'deterministic_algorithms': True},
            monitored_evaluation=dict(probe_sha256='probe', prompts_sha256='prompts',
                code_sha256='original', generation_steps=[250,2500,5000,*range(10000,55001,5000),57000])))
        self.meta = dict(training_contract=self.parent, step=7000, world_size=8,
                         config=cfg, sampler_version=2, tokens_per_step=2097152)

    def test_candidate_preserves_parent_and_absolute_accounting(self):
        before = deepcopy(self.meta)
        candidate, report = build_candidate(self.meta, 40000)
        self.assertEqual(self.meta, before)
        self.assertEqual(report['decay_start_step'], 34000)
        self.assertEqual(report['total_tokens'], 83886080000)
        self.assertEqual(report['committed_tokens'], 14680064000)
        self.assertFalse(report['runtime_applied'])
        self.assertFalse(report['distributed_resume_verified'])
        self.assertNotEqual(candidate['id'], self.parent['id'])

    def test_actual_trainer_lr_entire_committed_prefix_and_boundaries(self):
        # Execute only the real pure function, without importing CUDA/torch.
        tree = ast.parse(Path('train/train.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'lr_at')
        ns = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'actual-lr_at', 'exec'), ns)
        lr = ns['lr_at']
        c, _ = build_candidate(self.meta, 40000)
        old, new = self.parent['config'], c['config']
        self.assertTrue(all(lr(i, old) == lr(i, new) for i in range(34000)))
        self.assertEqual(lr(34000, new), 3e-4)
        self.assertLess(lr(34001, new), lr(34001, old))
        self.assertAlmostEqual(lr(39999, new), .000030045)
        self.assertAlmostEqual(lr(40000, new), .00003)

    def test_reject_mutated_inputs_geometry_code_and_evaluation(self):
        c, _ = build_candidate(self.meta, 40000)
        cases = [(['config','beta2'], .98), (['config','seed'], 17),
                 (['world_size'], 4), (['files','input','sha256'], 'changed'),
                 (['mix','tokenizer_sha256'], 'changed'), (['source_order'], ['en','ko','code','math']),
                 (['holdout'], ['different']), (['code','train/train.py'], 'different'),
                 (['execution','deterministic_algorithms'], False),
                 (['monitored_evaluation','prompts_sha256'], 'changed'),
                 (['monitored_evaluation','code_sha256'], 'different'),
                 (['monitored_evaluation','generation_steps'], [250,40000])]
        for path, value in cases:
            with self.subTest(path=path):
                bad = deepcopy(c); dest = bad
                for key in path[:-1]: dest = dest[key]
                dest[path[-1]] = value
                with self.assertRaises(ValueError): validate_transition(self.parent, sealed(bad), 7000)

    def test_reject_stale_identity_and_corrupt_metadata(self):
        for key, value in [('world_size',4), ('sampler_version',1), ('tokens_per_step',1),
                           ('step',True), ('step',34001), ('step',40000)]:
            with self.subTest(key=key, value=value):
                bad=deepcopy(self.meta); bad[key]=value
                with self.assertRaises(ValueError): build_candidate(bad, 40000)
        bad=deepcopy(self.meta);bad['training_contract']['id']='wrong'
        with self.assertRaises(ValueError):build_candidate(bad,40000)
        for target in [True, 0, 6000, 57000, 60000, 40000.0]:
            with self.subTest(target=target):
                with self.assertRaises(ValueError):build_candidate(self.meta,target)

    def test_exact_decay_boundary_is_valid_but_changed_history_is_not(self):
        meta=deepcopy(self.meta); meta['step']=34000
        build_candidate(meta, 40000)  # committed steps0..33999 still match.
        meta['step']=34001
        with self.assertRaises(ValueError):build_candidate(meta,40000)

    def test_requested35k_accounting_full_lr_prefix_and_final_boundaries(self):
        from train.horizon_target import (TARGET_STEPS, TOTAL_TOKENS, DECAY_START_STEP,
                                         FINAL_CHECKPOINT, generation_schedule)
        self.assertEqual((TARGET_STEPS,TOTAL_TOKENS,DECAY_START_STEP,FINAL_CHECKPOINT),
                         (35000,73400320000,29750,'ckpt_0035000'))
        candidate,report=build_candidate(self.meta,TARGET_STEPS)
        self.assertEqual(report['total_tokens'],73400320000)
        self.assertEqual(report['decay_start_step'],29750)
        self.assertEqual(candidate['monitored_evaluation']['generation_steps'],
                         generation_schedule(self.parent['monitored_evaluation']['generation_steps']))
        tree=ast.parse(Path('train/train.py').read_text())
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='lr_at')
        ns={};exec(compile(ast.Module(body=[function],type_ignores=[]),'actual-lr_at','exec'),ns)
        lr=ns['lr_at'];old,new=self.parent['config'],candidate['config']
        self.assertTrue(all(lr(i,old)==lr(i,new) for i in range(29750)))
        self.assertEqual(lr(29750,new),.0003)
        self.assertLess(lr(29751,new),lr(29751,old))
        self.assertAlmostEqual(lr(34999,new),.00003005142857142857)
        self.assertAlmostEqual(lr(35000,new),.00003)
        boundary=deepcopy(self.meta);boundary['step']=29750
        build_candidate(boundary,35000)
        boundary['step']=29751
        with self.assertRaises(ValueError):build_candidate(boundary,35000)


if __name__ == '__main__':
    unittest.main()
