from pathlib import Path
import json,os
from huggingface_hub import HfApi,CommitOperationAdd
from huggingface_hub.utils import RepositoryNotFoundError
from common.data_contracts import atomic_json,sha256_file
from scripts.verify_47000_hub_files import verify_files,file_list
out=Path(__file__).resolve().parent
p=out.parent/'jansori-proposal-v1'
read=lambda f: json.loads(f.read_text())
approved=read(p/'upload-authorization.json')
m=read(p/'manifest.json')
repo=m['repo']; files=m['files']; payload=p/'payload'
assert sha256_file(p/'manifest.json')==approved['manifest_sha256']
assert repo==approved['repo']=='pathcosmos/JANSORI' and m['visibility']==approved['visibility']=='public' and m['license']==approved['license']=='apache-2.0'
assert len(files)==approved['files'] and sum(x['bytes'] for x in files.values())==approved['bytes']
assert set(files)=={x.name for x in payload.iterdir()}
file_list('combined',files,True); verify_files('combined',payload,files)
api=HfApi(token=os.environ['HF_TOKEN'])
assert api.whoami()['name']=='pathcosmos'
try:
 info=api.model_info(repo)
except RepositoryNotFoundError:
 api.create_repo(repo,repo_type='model',private=False,exist_ok=False)
 info=api.model_info(repo)
assert not info.private
assert set(x.rfilename for x in info.siblings)<={'.gitattributes'},'Nonempty repository; inspect existing commit'
atomic_json(out/'repository.json',dict(repo=repo,initial_sha=info.sha,private=info.private))
commit=api.create_commit(repo,operations=[CommitOperationAdd(path_in_repo=name,path_or_fileobj=payload/name) for name in sorted(files)],commit_message='Release JANSORI 3B Base at 47,000 steps in BF16, Q8_0 and Q4_K_M',parent_commit=info.sha)
atomic_json(out/'commit.json',dict(repo=repo,commit=commit.oid,url=commit.commit_url,manifest_sha256=approved['manifest_sha256']))
print('Published',commit.oid,flush=True)
