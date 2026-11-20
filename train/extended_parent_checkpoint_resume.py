"""57k parent-resume adapter; all native scope/stat/load guards are preserved.

No launch, stop, copy or deletion is performed. The controller/worker must still
certify the full parent and sealed ledger and admit the distributed transition.
"""
from copy import deepcopy

from common.data_contracts import identity
from train.extended_horizon_worker_contract import validate_bound_candidate
from train.parent_checkpoint_resume import ExternalParentResume


class ExtendedParentResume(ExternalParentResume):
    def __init__(self, *, original_contract, **kwargs):
        self.original = deepcopy(original_contract)
        if kwargs.get('execution_binding') is None:
            raise ValueError('57k parent resume requires an explicit execution binding')
        super().__init__(**kwargs)

    def validate_transition(self):
        result = validate_bound_candidate(self.meta, self.original, self.contract,
                                          self.execution_binding)
        if identity(self.proof) != self.execution_binding['parent_certificate_id']:
            raise ValueError('Bound parent certificate content differs')
        return result
