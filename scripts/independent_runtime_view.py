"""Read-only consumer of the independently accepted main runtime.

No parent checkpoint/local history payload reads, signals, launches or pruning.
A saved target is not a final audit/quality/Goal-completion claim.
"""
import json
from pathlib import Path
import re

from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import exact_path,records
from scripts.horizon_process_handoff import process_record,same_identity
from scripts.horizon_probe_process import OWNER_ENV,tagged_pids
from train.independent_runtime_contract import validate_contract,validate_attempt,verify_consumption_rows,MODULE,SUPERVISOR


def stamp(path):
    path=exact_path(path);s=path.stat()
    if not path.is_file():raise ValueError('Runtime evidence must be a regular file')
    return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def json_file(path):
    path=exact_path(path);before=stamp(path)
    if before[2]>16*2**20:raise ValueError('Runtime metadata size limit')
    value=json.loads(path.read_text())
    if stamp(path)!=before:raise ValueError('Runtime metadata changed while reading')
    return value


def matching_process(expected):
    try:actual=process_record(expected['pid'])
    except (FileNotFoundError,ProcessLookupError,ValueError):return None
    return actual if same_identity(expected,actual) else None


def stopped_identities(rows):
    for row in rows:
        pid,ticks=row['pid'],row['start_ticks']
        if type(pid) is not int or pid<=1 or type(ticks) is not int or ticks<=0:
            raise ValueError('Invalid completed process identity')
        try:current=int(Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19])
        except FileNotFoundError:continue
        if current==ticks:return False
    return True


class RuntimeView:
    def __init__(self,control):
        self.control=exact_path(resolve(control));self.path=self.control/'runtime_contract.json'
        self.runtime,self.contract,self.acceptance=validate_contract(self.path)
        self.binding=self.runtime['binding'];self.main=resolve(self.binding['main_run'])
        self.config=json_file(self.main/'main.json')
        from train.extended_handoff import validate_configuration
        validate_configuration(self.config,self.contract)
        self.digest=sha256_file(self.path);self.attempts={}
        immutable={str(self.path),str(self.main/'main.json'),str(self.main/'contract.json'),self.runtime['authorization']['path']}
        immutable.update(self.contract['code']);immutable.update(self.contract['evidence']);immutable.update(self.binding['code'])
        from scripts.seal_independent_resume_result import IMPLEMENTATIONS as acceptance_code
        from scripts.freeze_independent_prefix import IMPLEMENTATIONS as prefix_code
        immutable.update(acceptance_code);immutable.update(prefix_code)
        archive=resolve(self.binding['independent8gpu']['run'])/'accepted_evidence'
        self.archives=[archive,archive.parent/'frozen_prefix']
        self.archive_files={str(a):sorted(str(p) for p in a.rglob('*') if p.is_file()) for a in self.archives}
        for names in self.archive_files.values():immutable.update(names)
        self.pins={str(resolve(p)):stamp(resolve(p)) for p in immutable}
        # Close the admission-to-stat snapshot race once, outside the30s loop.
        if validate_contract(self.path)!=(self.runtime,self.contract,self.acceptance):
            raise ValueError('Runtime admission changed while pinning consumer evidence')
        self.unchanged()

    def unchanged(self):
        for archive,names in self.archive_files.items():
            if sorted(str(p) for p in Path(archive).rglob('*') if p.is_file())!=names:
                raise ValueError('Historical acceptance file set changed')
        for path,before in self.pins.items():
            if stamp(Path(path))!=before:raise ValueError('Admitted runtime evidence changed: '+path)
        owner=json_file(self.main/'training/training_contract.json')
        if owner['id']!=self.acceptance['parent_training_contract_id']:
            raise ValueError('Original training contract identity changed')

    def attempt(self):
        pointer=json_file(self.control/'current_attempt.json')
        path=exact_path(resolve(pointer['execution']))
        if (pointer.get('runtime_contract_sha256')!=self.digest or path.parent.parent!=self.control/'attempts' or
                path.name!='execution.json' or sha256_file(path)!=pointer['execution_sha256']):
            raise ValueError('Current attempt pointer differs from runtime')
        key=str(path)
        if key not in self.attempts:
            attempt,validated,_=validate_attempt(path)
            if validated[0]!=self.runtime or attempt['main_run']!=str(self.main):
                raise ValueError('Current attempt belongs to another runtime')
            handle_path=path.parent/'launch/handle.json';handle=json_file(handle_path)
            if (not re.fullmatch('[0-9a-f]{32}',handle['owner']) or handle['pid']!=handle['process']['pid'] or
                    type(handle['process']['start_ticks']) is not int or handle['process']['start_ticks']<=0 or
                    handle['command'][-4:]!=['--module',MODULE,'--execution',str(path)]):
                raise ValueError('Owned launcher handle differs')
            files=[path,handle_path,resolve(attempt['revision']),resolve(attempt['shared_admission']['path'])]
            self.attempts[key]=dict(path=path,value=attempt,handle=handle,pins={str(p):stamp(p) for p in files})
        current=self.attempts[key]
        for p,before in current['pins'].items():
            if stamp(Path(p))!=before:raise ValueError('Immutable attempt evidence changed')
        return current

    def consumption(self,current):
        path=current['path'];attempt=current['value'];handle=current['handle'];folder=path.parent/'consumption'
        acknowledgement=folder/'supervisor_verified.json'
        if not acknowledgement.exists():return None
        paths=[folder/f'rank{rank}.json' for rank in range(8)]
        rows=[json_file(p) for p in paths];proof=verify_consumption_rows(attempt,rows,owner=handle['owner'])
        marker=folder/'all_ranks.json'
        if json_file(marker)!=proof:raise ValueError('Worker consumption marker differs')
        expected=dict(proof=proof,receipts={str(p):sha256_file(p) for p in paths+[marker]})
        if json_file(acknowledgement)!=expected:raise ValueError('Supervisor consumption ACK differs')
        for row in rows:
            if (row['launcher_pid']!=handle['pid'] or row['launcher_start_ticks']!=handle['process']['start_ticks']):
                raise ValueError('Consumption launcher binding differs')
        return dict(proof=proof,rows=rows,acknowledgement_sha256=sha256_file(acknowledgement))

    def processes(self,current,consumed):
        attempt=current['value'];handle=current['handle'];path=current['path']
        supervisor=matching_process(attempt['supervisor']);launcher=None;ranks=[]
        if supervisor is not None:
            args=supervisor['argv']
            if (not any(resolve(arg)==resolve(SUPERVISOR) for arg in args) or args.count('--control-dir')!=1 or
                    resolve(args[args.index('--control-dir')+1])!=self.control):supervisor=None
        try:p=process_record(handle['pid'])
        except (FileNotFoundError,ProcessLookupError,ValueError):p=None
        if p is not None:
            args=p['argv']
            if (p['start_ticks']==handle['process']['start_ticks'] and p['ppid']==attempt['supervisor']['pid'] and
                    'torch.distributed.run' in args and '--nproc_per_node=8' in args and
                    args[-4:]==['--module',MODULE,'--execution',str(path)]):
                environment=Path(f"/proc/{p['pid']}/environ").read_bytes().split(b'\0')
                if (OWNER_ENV+'='+handle['owner']).encode() in environment:launcher=p
        if consumed is not None:
            for row in consumed['rows']:
                try:p=process_record(row['pid'])
                except (FileNotFoundError,ProcessLookupError,ValueError):continue
                if (p['start_ticks']==row['start_ticks'] and p['ppid']==handle['pid'] and
                        p['argv'][-3:]==[MODULE,'--execution',str(path)]):
                    env=Path(f"/proc/{p['pid']}/environ").read_bytes().split(b'\0')
                    if all(f'{k}={v}'.encode() in env for k,v in [(OWNER_ENV,handle['owner']),('RANK',row['rank']),('LOCAL_RANK',row['rank']),('WORLD_SIZE',8)]):ranks.append(row['rank'])
        def summary(row):return {k:row[k] for k in ['pid','start_ticks','argv_sha256','state']} if row else None
        return dict(supervisor=summary(supervisor),launcher=summary(launcher),live_consumed_ranks=sorted(ranks),
            all8_consumed_live=consumed is not None and ranks==list(range(8)))

    def completion(self,current,consumed):
        marker=self.main/'.done'
        if not marker.exists():return None
        if consumed is None:raise ValueError('Completion without actual all8rank consumption ACK')
        report=json_file(self.main/'report.json');done=json_file(marker);a=current['value'];handle=current['handle']
        if (done!=dict(report_sha256=sha256_file(self.main/'report.json'),contract_sha256=self.binding['main_contract_sha256']) or
                report.get('status')!='main_target_completed_review_pending' or report.get('trained_tokens')!=119537664000 or
                resolve(report['final_checkpoint'])!=self.main/'training/ckpt_0057000' or
                report.get('runtime_contract_sha256')!=self.digest or resolve(report['execution'])!=current['path']):
            raise ValueError('Main target completion scope/binding differs')
        result=json_file(current['path'].parent/'launch/result.json')
        if (result['status']!='owned_main_exited_successfully' or result['exit_code']!=0 or
                result['owner']!=handle['owner'] or result['remaining_owned_processes']!=[]):
            raise ValueError('Main owned launch exit proof differs')
        rows=result['processes'];ids={(r['pid'],r['start_ticks']) for r in rows}
        required={(handle['pid'],handle['process']['start_ticks'])}|{(r['pid'],r['start_ticks']) for r in consumed['rows']}
        if len(ids)!=len(rows) or not required<=ids:raise ValueError('Main exit proof omits owned launcher/ranks')
        if not stopped_identities(rows) or tagged_pids(handle['owner']):return None
        cert=self.main/'checkpoint_certificates/ckpt_0057000.json';proof=json_file(cert)
        if (report['certificate_receipts'].get(cert.name)!=sha256_file(cert) or proof['status']!='passed_main_checkpoint_storage' or
                proof['step']!=57000 or proof['training_contract_id']!=a['training_contract_id'] or
                resolve(proof['checkpoint'])!=self.main/'training/ckpt_0057000'):
            raise ValueError('Final checkpoint producer certificate differs')
        records(proof)
        return dict(status='target_saved_final_review_pending',step=57000,report_sha256=done['report_sha256'],
            certificate_sha256=sha256_file(cert),payload_sha_refreshed=False,goal_complete=False)

    def observe(self):
        self.unchanged();state=json_file(self.main/'state.json')
        if not (self.control/'current_attempt.json').exists():
            return dict(phase='await_independent_execution',source_state=state,processes=None,completion=None,consumption=None)
        current=self.attempt();consumed=self.consumption(current);processes=self.processes(current,consumed)
        completed=self.completion(current,consumed)
        self.unchanged()
        return dict(phase=state.get('phase'),source_state=state,execution=str(current['path']),processes=processes,
            consumption=({k:v for k,v in consumed.items() if k!='rows'} if consumed else None),completion=completed)
