"""Run a pinned upstream llama.cpp BPE recognition method on the real tokenizer.

This is a tokenizer-only converter gate, not a complete GGUF conversion or an
Ollama runner test. No upstream function body or tokenizer metadata is patched.
"""
import argparse
import ast
import hashlib
import json
import logging
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,atomic_json,sha256_file


def load_method(source):
    tree=ast.parse(source.read_text())
    methods=[node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name=='get_vocab_base_pre']
    if len(methods)!=1:raise ValueError('Expected one upstream recognition method')
    method=methods[0]
    if method.decorator_list or any(isinstance(n,(ast.Import,ast.ImportFrom)) for n in ast.walk(method)):
        raise ValueError('Unexpected upstream method shape')
    namespace={'sha256':hashlib.sha256,'logger':logging.getLogger('upstream_gguf_probe')}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),namespace)
    return namespace['get_vocab_base_pre'],method


def main(root, tokenizer):
    root=resolve(root);tokenizer=resolve(tokenizer)
    if (root/'report.json').exists() or (root/'hf_tokenizer').exists():
        raise FileExistsError('Preserve prior compatibility proof')
    started=time.monotonic();verified=[]
    for manifest in ['source_manifest.json','source_manifest_additional.json']:
        for row in json.loads((root/manifest).read_text()):
            p=resolve(row['local']);b=p.read_bytes()
            actual=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
            if actual!=row['git_blob_sha']:raise ValueError('Pinned upstream source bytes differ')
            verified.append(dict(**row,bytes=len(b),sha256=hashlib.sha256(b).hexdigest()))
    before=sha256_file(tokenizer)
    from inference.hf_export import write_tokenizer
    from transformers import AutoTokenizer
    from tokenizers import Tokenizer
    target=root/'hf_tokenizer';target.mkdir()
    packaged=write_tokenizer(tokenizer,target,SimpleNamespace(vocab_size=65536,max_seq_len=4096))
    fast=AutoTokenizer.from_pretrained(target,local_files_only=True,trust_remote_code=False)
    raw=Tokenizer.from_file(str(tokenizer))
    source=root/'sources/llama/conversion/base.py';function,node=load_method(source)
    assignments=[n for n in node.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='chktxt' for t in n.targets)]
    if len(assignments)!=1:raise ValueError('Upstream identification probe changed')
    text=ast.literal_eval(assignments[0].value)
    ids=fast.encode(text)
    if ids!=raw.encode(text,add_special_tokens=False).ids:raise ValueError('Tokenizer packaging changed probe IDs')
    probe_hash=hashlib.sha256(str(ids).encode()).hexdigest()
    try:
        pre=function(None,fast)
        verdict=dict(status='recognized_tokenizer_full_conversion_pending',gguf_pre=pre)
    except NotImplementedError as exc:
        if str(exc)!='BPE pre-tokenizer was not recognized - update get_vocab_base_pre()':raise
        verdict=dict(status='rejected_by_pinned_upstream_recognition',error_type=type(exc).__name__,error=str(exc))
    # Source inspection only: this Go parser is not compiled/executed here.
    go_source=root/'sources/ollama/mlxrunner/tokenizer/tokenizer_pretokenizer.go'
    go=go_source.read_text()
    switch=go.split('switch raw.Type {',1)[1].split('func compileSplit',1)[0]
    unsupported_digits=('case "Digits"' not in switch and 'unsupported pretokenizer' in switch)
    if sha256_file(tokenizer)!=before or sha256_file(target/'tokenizer.json')!=before:
        raise ValueError('Original tokenizer bytes changed')
    report=dict(status='tokenizer_compatibility_probe_completed_runtime_pending',
        tokenizer_sha256=before,tokenizer_packaging=packaged,source_files=verified,
        upstream_method=dict(path=str(source),first_line=node.lineno,last_line=node.end_lineno,body_modified=False),
        actual_recognition_execution=verdict,probe_text=text,probe_token_ids=ids,probe_sha256=probe_hash,
        raw_and_hf_ids_equal=True,ollama_source_inspection=dict(digits_rejected_by_default_branch=unsupported_digits,
            go_parser_executed=False,path=str(go_source)),
        actual_gguf_conversion_executed=False,actual_ollama_runner_executed=False,
        actual3b_verified=False,goal_complete=False,seconds=time.monotonic()-started)
    atomic_json(root/'report.json',report)
    print(json.dumps({k:report[k] for k in ['status','actual_recognition_execution','probe_sha256','ollama_source_inspection','seconds']},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',default='runs/main_training_35k_plan_v1/ollama_compatibility_v2')
    p.add_argument('--tokenizer',default='tokenizer/candidate_v1/tokenizer.json')
    a=p.parse_args();main(a.root,a.tokenizer)
