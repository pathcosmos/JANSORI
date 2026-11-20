"""50k transition adapter retaining native full-state load and scope guards."""
from common.data_contracts import identity
from train.parent_checkpoint_resume import ExternalParentResume
from train.budget_horizon_contract import validate_bound_candidate


class BudgetParentResume(ExternalParentResume):
    def validate_transition(self):
        if self.execution_binding is None:
            raise ValueError('50k parent resume requires an execution binding')
        result=validate_bound_candidate(self.meta,self.contract,self.execution_binding)
        if identity(self.proof)!=self.execution_binding['parent_certificate_id']:
            raise ValueError('50k parent certificate content differs')
        return result
