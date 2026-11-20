"""Read-only admission of the completed 50K HF export for future Ollama work.

This is not a converter, a publisher, or proof that an Ollama model works.
"""
import json

from common.data_contracts import resolve, sha256_file


def admit_final_hf():
    from scripts.export_budget_hf import load_contract, verify_result

    root = resolve('artifacts/3b_base_50k_v1')
    marker = root / '.done'
    if not marker.is_file():
        raise ValueError('Final 50K HF completion is unavailable')
    # Never invoke the controller: if .done disappears between a caller's
    # check and controller entry, it can take the producer-start branch.
    files = (marker, root / 'contract.json', root / 'report.json')
    before = {str(path): sha256_file(path) for path in files}
    contract = load_contract(root)
    if 'final_storage' in contract['source']:
        from scripts.completed_budget_storage import admit_reference
        admit_reference(contract['source']['main_run'], contract['source']['final_storage'])
    result = verify_result(root, contract)
    report = json.loads((root / 'report.json').read_text())
    recorded = json.loads(marker.read_text())
    if (recorded != {'report_sha256': before[str(root / 'report.json')],
                     'contract_sha256': before[str(root / 'contract.json')]}
            or any(report.get(k) != v for k, v in result.items())
            or before != {str(path): sha256_file(path) for path in files}):
        raise ValueError('HF completion evidence changed')
    binding = report['source_binding']
    if (report['status'] != 'passed_final50k_hf_export_runtime_pending'
            or binding['status'] != 'verified_actual_final50k_source'
            or binding['step'] != 50000
            or binding['committed_tokens'] != 104857600000
            or binding['committed_step_rank_pairs'] != 400000):
        raise ValueError('Final HF source evidence differs')
    return {
        'status': 'verified_hf_input_only',
        'hf_directory': str(root / 'hf'),
        'source_binding': binding,
        'export_report_sha256': sha256_file(root / 'report.json'),
        'completion_marker_sha256': sha256_file(marker),
        'release_manifest_sha256': report['publication_manifest_sha256'],
        'ollama_verified': False,
        'goal_complete': False,
    }
