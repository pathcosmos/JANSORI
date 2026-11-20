"""Versioned35k entry with an explicit protected-parent and lineage handoff.

Not used by the live57k producer. Requires a sealed controller handoff and
separate actual8GPU validation before production use. Native training stays unchanged.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, atomic_json, identity, sha256_file
from train.horizon_target import TARGET_STEPS


def validate_configuration(config, contract):
    from scripts.plan_main_training import geometry
    g = geometry(config)
    expected = dict(model='3b', vocab_size=65536, seq_len=4096, micro_bsz=4,
                    global_tokens=2097152, compile=False, sampler_version=2,
                    max_steps=TARGET_STEPS, lr=.0003, warmup=1000, decay_frac=.15,
                    min_lr_ratio=.1, eval_every=250, ckpt_every=500, save_final=True)
    if any(type(config.get(k)) is not type(v) or config[k] != v for k,v in expected.items()):
        raise ValueError('Unreviewed35k training geometry or cadence')
    if (type(config.get('keep_ckpts')) is not int or config['keep_ckpts'] <= TARGET_STEPS or
            config.get('log_every') != 10):
        raise ValueError('Native pruning must remain disabled; log cadence must be10')
    if contract.get('main_training_authorized') is not True:
        raise ValueError('Missing main training authorization')
    purpose = contract.get('purpose')
    if purpose == 'main_training':
        if config.get('stop_after') is not None:
            raise ValueError('Production cannot silently shorten the35k target')
    elif purpose == 'horizon_validation':
        begin = contract['horizon_execution']['parent_step']
        if (type(config.get('stop_after')) is not int or
                not begin < config['stop_after'] <= begin+2):
            raise ValueError('Horizon diagnostic is limited to two absolute updates')
    else:
        raise ValueError('Unreviewed horizon execution purpose')
    schedule = contract.get('generation_steps')
    if (not schedule or schedule != sorted(set(schedule)) or schedule[-1] != TARGET_STEPS or
            any(type(s) is not int or not 0 < s <= TARGET_STEPS or s % 250 for s in schedule)):
        raise ValueError('Generation schedule must include final35k on evaluation boundaries')
    return g


def load_handoff(root, contract, config):
    from train.parent_checkpoint_resume import canonical
    from train.horizon_worker_contract import BRIDGE_CODE, executable_candidate
    root = canonical(root)
    if root.parent != canonical(resolve('runs')):
        raise ValueError('Horizon worker requires an exact project runs child')
    if canonical(resolve(config['out_dir'])) != root/'training':
        raise ValueError('Child output must be the exact new-run training namespace')
    receipt_path = root/'parent_handoff.json'
    if contract['evidence'].get(str(receipt_path)) != sha256_file(receipt_path):
        raise ValueError('Parent handoff is not bound in the execution contract')
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('status') != 'sealed_parent_handoff':
        raise ValueError('Parent handoff has not been sealed')
    binding = contract['horizon_execution']
    if binding != receipt['execution_binding']:
        raise ValueError('Parent handoff execution binding differs')
    for name in BRIDGE_CODE:
        if contract['code'].get(name) != binding['code'][name] or sha256_file(resolve(name)) != binding['code'][name]:
            raise ValueError('Unbound or changed horizon implementation: '+name)
    for name in ['train/final_resume_probe.py', 'scripts/audit_main_training.py',
                 'scripts/tiered_checkpoint_retention.py']:
        if contract['code'].get(name) != sha256_file(resolve(name)):
            raise ValueError('Unbound horizon admission implementation: '+name)
    meta = receipt['parent_metadata']
    candidate = executable_candidate(meta, binding)
    if contract['purpose'] == 'main_training':
        validation_path = canonical(resolve(contract['horizon_validation_report']))
        if contract['evidence'].get(str(validation_path)) != sha256_file(validation_path):
            raise ValueError('Actual horizon validation report is not bound')
        validation = json.loads(validation_path.read_text())
        if (validation.get('status') != 'passed_actual_8rank_horizon_resume' or
                validation.get('world_size') != 8 or
                validation.get('parent_contract_id') != meta['training_contract']['id'] or
                validation.get('child_contract_id') != candidate['id'] or
                validation.get('parent_step') != meta['step'] or
                any(validation.get(k) is not True for k in
                    ['model_optimizer_rng_exact', 'next_update_exact', 'split_resume_exact'])):
            raise ValueError('Actual8GPU full-state/next-update/split-resume validation missing')
        evidence = validation.get('evidence', {})
        if not evidence:
            raise ValueError('Actual8GPU validation lacks underlying evidence')
        for name, digest in evidence.items():
            if sha256_file(resolve(name)) != digest:
                raise ValueError('Actual8GPU validation evidence changed')
    parent = canonical(resolve(receipt['parent_checkpoint']))
    if parent != root/'parent_checkpoint'/f"ckpt_{meta['step']:07d}":
        raise ValueError('Parent must be a protected NFS copy in the new-run namespace')
    if root.stat().st_dev != parent.stat().st_dev:
        raise ValueError('Protected parent must use the permanent run filesystem')
    # Every attempt checks matching parent-process identities, not just a lock.
    processes = receipt['stopped_parent_processes']
    if set(processes) != {'supervisor','worker',*[f'rank{r}' for r in range(8)]}:
        raise ValueError('Missing parent supervisor/worker/8rank stop identities')
    if len({info['pid'] for info in processes.values()}) != 10:
        raise ValueError('Parent process identities are not distinct')
    for info in processes.values():
        pid,ticks = info['pid'],info['start_ticks']
        if type(pid) is not int or pid < 1 or type(ticks) is not int or ticks < 1:
            raise ValueError('Invalid parent process identity')
        stat = Path('/proc')/str(pid)/'stat'
        try:
            text = stat.read_text(); current = int(text[text.rfind(')')+2:].split()[19])
        except FileNotFoundError:
            continue
        if current == ticks:
            raise ValueError('A bound parent process is still present; handoff cannot start')
    manifest_path = canonical(root/'lineage/manifest.json')
    if sha256_file(manifest_path) != binding['parent_ledger_manifest_sha256']:
        raise ValueError('Parent lineage manifest changed')
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get('through_step') != meta['step'] or
            manifest.get('committed_tokens') != meta['step']*2097152 or
            manifest.get('committed_step_rank_pairs') != meta['step']*8 or
            manifest.get('training_contract_id') != meta['training_contract']['id']):
        raise ValueError('Parent committed prefix differs')
    records = manifest.get('files',{})
    required = {'audit.json','trace.jsonl',*[f'rank{r}.json' for r in range(8)]}
    if set(records) != required:
        raise ValueError('Missing sealed parent audit/trace/8rank ledgers')
    for name,digest in records.items():
        if sha256_file(canonical(root/'lineage'/name)) != digest:
            raise ValueError('Parent lineage file changed: '+name)
    # Recompute the committed prefix from the sealed original attempts. A JSON
    # pass flag or a summary token count alone is not enough to admit a handoff.
    from scripts.audit_main_training import aggregate
    attempts = []
    for rank in range(8):
        entries = json.loads((root/'lineage'/f'rank{rank}.json').read_text())
        if not isinstance(entries, list) or not entries:
            raise ValueError('Missing original parent rank attempts')
        for item in entries:
            if item['record']['rank'] != rank:
                raise ValueError('Parent rank ledger filename differs from its contents')
            attempts.append((item['attempt'], item['record']))
    parent_contract = meta['training_contract']
    counts = aggregate(attempts, world=8, through_step=meta['step'],
        target_steps=parent_contract['config']['max_steps'], seq_len=parent_contract['config']['seq_len'],
        global_tokens=2097152, sources=parent_contract['source_order'],
        generation_steps=parent_contract['monitored_evaluation']['generation_steps'], contract_id=parent_contract['id'])
    audit = json.loads((root/'lineage/audit.json').read_text())
    if audit.get('counts') != counts:
        raise ValueError('Sealed parent audit differs from recomputed rank/update coverage')
    proof_path = root/'parent_certificate.json'
    if sha256_file(proof_path) != binding['parent_certificate_sha256']:
        raise ValueError('Parent content certificate changed')
    proof = json.loads(proof_path.read_text())
    if (proof.get('step') != meta['step'] or
            proof.get('training_contract_id') != meta['training_contract']['id']):
        raise ValueError('Parent certificate identity differs from sealed metadata')
    return receipt, candidate, proof


def parent_resume(receipt, expected_contract, parent_proof, config, contract):
    """Shared real worker initialization, also exercised by handoff integration tests."""
    from train.parent_checkpoint_resume import ExternalParentResume
    if not isinstance(receipt.get('parent_stats'), dict) or not receipt['parent_stats']:
        raise ValueError('Handoff lacks verified parent file-stat snapshot')
    return ExternalParentResume(parent_checkpoint=resolve(receipt['parent_checkpoint']),
        destination=resolve(config['out_dir']), parent_metadata=receipt['parent_metadata'],
        child_contract=expected_contract, parent_proof=parent_proof,
        verified_parent_stats=receipt['parent_stats'], execution_binding=contract['horizon_execution'])


def worker(root, config_path, attempt):
    import torch
    import torch.distributed as dist
    from train import train as trainer
    from train.deterministic_train import configure
    from train.parallel_contract import training_contract as parallel_contract
    from train.probe import verify_probe, evaluate_sources
    from train.rehearsal import SourceLedger, observed_sampler, step_and_record, distributed_generate
    from tokenizers import Tokenizer
    root, config_path = resolve(root), resolve(config_path)
    contract = json.loads((root/'contract.json').read_text())
    if attempt != Path(attempt).name or attempt in ('.', '..'):
        raise ValueError('Invalid monitored attempt')
    expected = contract['configurations'].get(config_path.name)
    if config_path.parent != root or expected != sha256_file(config_path):
        raise ValueError('Unbound monitored configuration')
    for path, digest in {**contract['code'], **contract['evidence']}.items():
        if sha256_file(resolve(path)) != digest:
            raise ValueError('Monitored producer/input changed: '+path)
    config = json.loads(config_path.read_text()); validate_configuration(config, contract)
    receipt, expected_training_contract, parent_proof = load_handoff(root, contract, config)
    resume = parent_resume(receipt, expected_training_contract, parent_proof, config, contract)
    trainer.complete_checkpoints = resume.checkpoints
    trainer.validate_resume = resume.validate
    original_load = trainer.dcp.load
    trainer.dcp.load = lambda *args, **kwargs: resume.load(original_load, *args, **kwargs)

    probe = verify_probe(root/'probe')
    mix = json.loads(resolve(config['mix']).read_text())
    if (config['holdout'] != probe['holdout'] or config['seq_len'] != probe['seq_len'] or
            mix['tokenizer_sha256'] != probe['tokenizer_sha256']):
        raise ValueError('Monitored probe/tokenizer differs')
    def fresh(c, world, hold_files):
        if world != 8:
            raise ValueError('Actual monitored3B requires8GPU world')
        return parallel_contract(c, world, hold_files,
                    expected_files=json.loads((root/'expected_tokens.json').read_text()),
                    observe=lambda metrics:atomic_json(root/(attempt+'-input_hash.json'), metrics))
    trainer.training_contract = fresh; configure()
    original_contract = trainer.training_contract
    def training_contract(c, world, hold_files):
        value = original_contract(c, world, hold_files); value.pop('id')
        value['monitored_evaluation'] = dict(probe_sha256=sha256_file(root/'probe/probe.json'),
                    prompts_sha256=sha256_file(resolve(contract['prompts'])),
                    generation_steps=contract['generation_steps'], code_sha256=sha256_file(Path(__file__)),
                    source_hooks_sha256=sha256_file(resolve('train/rehearsal.py')))
        value['horizon_transition'] = contract['horizon_execution']
        value['id'] = identity(value)
        if value != expected_training_contract:
            raise ValueError('Fresh input/code contract differs from the bound35k candidate')
        return value
    trainer.training_contract = training_contract
    ledger = SourceLedger(mix['sources']); rank = int(os.environ['RANK'])
    trainer.MixSampler = observed_sampler(trainer.MixSampler, ledger)
    original_step = torch.optim.AdamW.step
    def step(optimizer, *args, **kwargs):
        return step_and_record(original_step, ledger, optimizer, *args, **kwargs)
    torch.optim.AdamW.step = step
    def persist(success=False, checkpoint_step=None):
        path = resolve(config['out_dir'])/'training_contract.json'
        cid = json.loads(path.read_text())['id'] if path.exists() else None
        atomic_json(root/'ledgers'/(attempt+f'-rank{rank}.json'),
                    dict(rank=rank, success=success, contract_id=cid, steps=ledger.steps,
                         generations=ledger.generations, checkpoint_step=checkpoint_step,
                         optimizer_initializations=ledger.optimizer_initializations))
    prompts = [json.loads(s) for s in resolve(contract['prompts']).read_text().splitlines() if s.strip()]
    if len(prompts) != 6 or len({r['id'] for r in prompts}) != 6 or {r['category'] for r in prompts} != {'ko', 'en'}:
        raise ValueError('Six bilingual fixed prompts required')
    tokenizer = Tokenizer.from_file(str(resolve(contract['tokenizer'])))
    eos = tokenizer.token_to_id('<|eos|>')
    if eos is None:
        raise ValueError('Final tokenizer lacks EOS')
    original_eval = trainer.evaluate_holdout
    def evaluate(model, fwd, hold, device):
        start = time.monotonic()
        def timed(*args):
            t = time.monotonic(); r = original_eval(*args)
            return dict(r, elapsed_seconds=time.monotonic()-t)
        result = evaluate_sources(timed, model, fwd, hold, device, probe)
        result['probe_eval_seconds'] = time.monotonic()-start
        step = ledger.steps[-1]['step']
        if step in contract['generation_steps']:
            t = time.monotonic(); generations = []
            for p in prompts:
                generated = distributed_generate(model, fwd, tokenizer.encode(p['text'], add_special_tokens=False).ids,
                            eos_id=eos, context_length=config['seq_len'], max_new_tokens=64, device=device)
                generations.append(dict(id=p['id'], category=p['category'], prompt=p['text'],
                            text=tokenizer.decode(generated['generated_ids'], skip_special_tokens=False), **generated))
            result.update(fixed_generations=generations, generation_seconds=time.monotonic()-t)
            ledger.generations.append(dict(step=step, sha256=identity(generations)))
        return result
    trainer.evaluate_holdout = evaluate
    original_save = trainer.dcp.save
    def save(*args, **kwargs):
        t = time.monotonic(); result = original_save(*args, **kwargs); payload = time.monotonic()-t
        t = time.monotonic(); persist(checkpoint_step=ledger.steps[-1]['step'])
        seconds = torch.tensor([payload, time.monotonic()-t], dtype=torch.float64,
                               device=torch.device('cuda', int(os.environ['LOCAL_RANK'])))
        dist.all_reduce(seconds, op=dist.ReduceOp.MAX)
        if rank == 0:
            with (root/'checkpoint_timing.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(checkpoint=Path(kwargs['checkpoint_id']).name,
                     payload_seconds_max_rank=float(seconds[0].item()),
                     source_ledger_seconds_max_rank=float(seconds[1].item()),
                     scope='Payload/ledger only; later RNG/meta publication excluded.'))+'\n')
        return result
    trainer.dcp.save = save
    success = False
    try:
        sys.argv = ['train/monitored_horizon.py', '--config', str(config_path)]
        trainer.main(); success = True
    finally:
        persist(success)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', required=True); p.add_argument('--config', required=True)
    p.add_argument('--attempt', required=True); a = p.parse_args()
    worker(a.run_dir, a.config, a.attempt)
