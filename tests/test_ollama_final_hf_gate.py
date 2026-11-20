import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.data_contracts import sha256_file
from scripts.ollama_final_hf_gate import admit_final_hf


class FinalHFGateTests(unittest.TestCase):
    def test_completed_receipt_and_changed_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract, report_file, marker = (root / 'contract.json', root / 'report.json', root / '.done')
            contract.write_text('{}')
            binding = dict(status='verified_actual_final50k_source', step=50000,
                           committed_tokens=104857600000, committed_step_rank_pairs=400000)
            report = dict(status='passed_final50k_hf_export_runtime_pending',
                          source_binding=binding, publication_manifest_sha256='fixture')
            report_file.write_text(json.dumps(report))
            marker.write_text(json.dumps(dict(report_sha256=sha256_file(report_file),
                                              contract_sha256=sha256_file(contract))))
            with patch('scripts.ollama_final_hf_gate.resolve', return_value=root), \
                 patch('scripts.export_budget_hf.load_contract', return_value={'source': {}}), \
                 patch('scripts.export_budget_hf.controller', side_effect=AssertionError('must not start')), \
                 patch('scripts.export_budget_hf.verify_result', return_value=report) as verify:
                self.assertEqual(admit_final_hf()['status'], 'verified_hf_input_only')
                marker.write_text('{}')
                with self.assertRaisesRegex(ValueError, 'completion evidence changed'):
                    admit_final_hf()
                self.assertEqual(verify.call_count, 2)

    def test_absent_marker_does_not_call_controller(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('scripts.ollama_final_hf_gate.resolve', return_value=Path(tmp)), \
             patch('scripts.export_budget_hf.controller', side_effect=AssertionError('must not start')):
            with self.assertRaisesRegex(ValueError, 'completion is unavailable'):
                admit_final_hf()

    def test_evidence_changed_during_verification_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract, report_file, marker = root / 'contract.json', root / 'report.json', root / '.done'
            contract.write_text('{}')
            binding = dict(status='verified_actual_final50k_source', step=50000,
                           committed_tokens=104857600000, committed_step_rank_pairs=400000)
            report = dict(status='passed_final50k_hf_export_runtime_pending',
                          source_binding=binding, publication_manifest_sha256='fixture')
            report_file.write_text(json.dumps(report))
            marker.write_text(json.dumps(dict(report_sha256=sha256_file(report_file),
                                              contract_sha256=sha256_file(contract))))

            def mutate_during_verification(*args):
                contract.write_text('{"changed": true}')
                return report

            with patch('scripts.ollama_final_hf_gate.resolve', return_value=root), \
                 patch('scripts.export_budget_hf.load_contract', return_value={'source': {}}), \
                 patch('scripts.export_budget_hf.verify_result', side_effect=mutate_during_verification):
                with self.assertRaisesRegex(ValueError, 'completion evidence changed'):
                    admit_final_hf()

    def test_wrong_final_step_is_rejected_even_with_valid_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract, report_file, marker = root / 'contract.json', root / 'report.json', root / '.done'
            contract.write_text('{}')
            report = dict(status='passed_final50k_hf_export_runtime_pending',
                          source_binding=dict(status='verified_actual_final50k_source', step=49999,
                                              committed_tokens=104857600000,
                                              committed_step_rank_pairs=400000),
                          publication_manifest_sha256='fixture')
            report_file.write_text(json.dumps(report))
            marker.write_text(json.dumps(dict(report_sha256=sha256_file(report_file),
                                              contract_sha256=sha256_file(contract))))
            with patch('scripts.ollama_final_hf_gate.resolve', return_value=root), \
                 patch('scripts.export_budget_hf.load_contract', return_value={'source': {}}), \
                 patch('scripts.export_budget_hf.verify_result', return_value=report):
                with self.assertRaisesRegex(ValueError, 'source evidence differs'):
                    admit_final_hf()


if __name__ == '__main__':
    unittest.main()
