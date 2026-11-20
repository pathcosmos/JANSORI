"""Recover a fresh anonymous Hub download from verified public bytes, fetching sparse gaps only."""
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
import requests
from common.data_contracts import atomic_json,sha256_file
from scripts.verify_47000_hub_files import verify_files,file_list
out=Path(__file__).resolve().parent
scratch=Path((out/'clean-root').read_text().strip())
c=json.loads((out/'commit.json').read_text())
manifest=out.parent/'jansori-proposal-v1/manifest.json'
inv=json.loads(manifest.read_text())['files']
root=scratch/'http-public';root.mkdir()
os.environ.update(HF_HOME=str(root),HF_HUB_DISABLE_XET='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',HF_HUB_DISABLE_PROGRESS_BARS='1')
from huggingface_hub import HfApi,snapshot_download
info=HfApi().model_info(c['repo'],revision=c['commit'],token=False,files_metadata=True)
assert info.sha==c['commit'] and not info.private and {x.rfilename for x in info.siblings}==set(inv)
subdir='models--'+c['repo'].replace('/','--')
original=scratch/'hf/hub'/subdir
blobs=root/'hub'/subdir/'blobs';blobs.mkdir(parents=True)
def stage(f):
    name=f.rfilename; row=inv[name]; sha=row['sha256']; digest=f.lfs.sha256 if f.lfs else f.blob_id
    assert f.size==row['bytes'] and (not f.lfs or f.lfs.sha256==sha)
    dest=blobs/digest
    choices=[(original/'blobs'/digest,'fresh HF download')]
    if name.endswith('.gguf'):choices.append((scratch/'ollama-store/blobs'/('sha256-'+sha),'fresh Ollama download'))
    record=dict(file=name,sha256=sha)
    for file,label in choices:
        if file.is_file() and file.stat().st_size==row['bytes'] and sha256_file(file)==sha:
            os.link(file,dest);record['source']=label;return record
    inc=original/'blobs'/(digest+'.incomplete')
    assert inc.is_file()
    subprocess.run(['cp','--sparse=always',str(inc),str(dest)],check=True)
    ranges=[]
    size=row['bytes']
    assert dest.stat().st_size==size
    with dest.open('r+b') as handle:
        pos=0
        while pos<size:
            try:start=os.lseek(handle.fileno(),pos,os.SEEK_DATA)
            except OSError as exc:
                if exc.errno!=errno.ENXIO:raise
                start=size
            if start>pos:ranges.append((max(0,pos-8192),min(size,start+8192)))
            if start==size:break
            pos=os.lseek(handle.fileno(),start,os.SEEK_HOLE)
        sess=requests.Session();sess.trust_env=False
        for lo,hi in ranges:
            url=f'https://huggingface.co/{c["repo"]}/resolve/{c["commit"]}/{name}'
            with sess.get(url,headers={'Range':f'bytes={lo}-{hi-1}','Accept-Encoding':'identity'},stream=True,timeout=(15,60)) as resp:
                if resp.status_code!=206 or resp.headers.get('Content-Range')!=f'bytes {lo}-{hi-1}/{size}':raise ValueError('Range response does not match request')
                handle.seek(lo)
                for block in resp.iter_content(1024*1024):handle.write(block)
                assert handle.tell()==hi
    assert sha256_file(dest)==sha,name
    return dict(**record,source='fresh HF download plus pinned HTTP ranges',ranges=ranges)
with ThreadPoolExecutor(max_workers=4) as pool: provenance=list(pool.map(stage,info.siblings))
path=Path(snapshot_download(c['repo'],revision=c['commit'],cache_dir=root/'hub',token=False,max_workers=4))
assert all((path/k).resolve().is_relative_to(root) for k in inv)
file_list('combined',inv,True);verify_files('combined',path,inv,allow_cache_links=True)
atomic_json(out/'anonymous-recovery.json',dict(provenance=provenance,cache=str(root)))
atomic_json(out/'range-snapshot.json',dict(scope='hub_snapshot',kind='combined',repo=c['repo'],commit=c['commit'],private=False,files=len(inv),bytes=sum(f['bytes'] for f in inv.values()),manifest_sha256=sha256_file(manifest),local_path=str(path),runtime_verified=False))
