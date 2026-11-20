"""Content verification for serial50k final consumers; native is handled by coordinator."""
import json
from pathlib import Path
from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import exact_path
from scripts.evaluate_final_rehearsal import check_bindings

def json_file(path):return json.loads(Path(path).read_text())

def verify_stage(row):
    name=row['name'];root=exact_path(row['output'])
    if name=='resume_plan':
        from scripts.run_budget_final_resume import load_plan
        plan=load_plan(root);check_bindings(plan)
        return dict(plan_sha256=sha256_file(root/'plan.json'))
    contract=json_file(root/'contract.json');check_bindings(contract)
    if name.endswith('_prepare'):
        marker=root/('controller_prepared.json' if name=='resume_prepare' else 'prepared.json')
        if json_file(marker)['contract_sha256']!=sha256_file(root/'contract.json'):
            raise ValueError('Stage preparation contract differs')
        return dict(contract_sha256=sha256_file(root/'contract.json'),prepared_sha256=sha256_file(marker))
    report=json_file(root/'report.json')
    if json_file(root/'.done')!=dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')):
        raise ValueError('Stage marker/report binding differs')
    if name=='native':
        from tokenizers import Tokenizer
        from scripts.evaluate_budget_native import verify_result
        from scripts.budget_independence_unavailable import verify_audit_runtime
        if (report['status']!='passed_actual_final3b_native_evaluation' or
                report['result_sha256']!=sha256_file(root/'3b_final.json') or
                report['final_training_audit_sha256']!=sha256_file(root/'final_training_audit.json')):
            raise ValueError('Native result/audit binding differs')
        main=resolve(contract['main_run'])
        if report['main_report_sha256']!=sha256_file(main/'report.json'):raise ValueError('Native main report differs')
        verify_audit_runtime(json_file(root/'final_training_audit.json'),main,contract['independent_runtime'])
        proof=json_file(main/'checkpoint_certificates/ckpt_0050000.json');result=json_file(root/'3b_final.json')
        verify_result(result,proof,Tokenizer.from_file(str(resolve(contract['tokenizer']))),contract['expected_targets'],
            json_file(resolve(contract['reference_report'])),expected_step=50000,expected_device='cuda')
        if any(report[k]!=result[k] for k in ['languages','aggregate']):raise ValueError('Native summary differs')
    elif name in ('resume','retention'):
        if name=='resume':from scripts.run_budget_final_resume import verify_completed
        else:from scripts.run_budget_retention_audit import verify_completed
        verify_completed(root,contract)
    else:
        if name=='hf':from scripts.export_budget_hf import verify_result
        elif name=='vllm':from scripts.run_budget_vllm import verify_result
        elif name=='serving':from scripts.run_budget_serving import verify_result
        else:raise ValueError('Unknown final stage')
        fresh=verify_result(root,contract)
        if any(report.get(k)!=v for k,v in fresh.items()):raise ValueError('Stage report differs from verified content')
    return dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json'))
