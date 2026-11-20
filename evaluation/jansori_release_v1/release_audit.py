"""Audit locally certified artifacts and the observed public release, then write the Korean report."""
from pathlib import Path
from datetime import datetime,timezone
import json
import socket
import os
from common.data_contracts import atomic_json,sha256_file
d=Path(__file__).resolve().parent
run=d.parent
p=run/'jansori-proposal-v1'
n=run/'nfs-release-v1'
read=lambda path:json.loads(path.read_text())
snap=read(d/'public-snapshot.json')
remote=read(d/'public-metadata.json')
public=read(d/'commit.json')
auth=read(p/'upload-authorization.json')
pull=read(d/'ollama-pull.json')
nfs=read(n/'manifest.json')
digest=sha256_file(n/'manifest.json')
assert digest==read(n/'.done')['manifest_sha256']
for k in ('repo','commit'):assert snap[k]==remote[k]==public[k]==pull[k]
assert remote['license']==auth['license']=='apache-2.0' and not snap['private'] and remote['private'] is False
assert snap['files']==auth['files']==18 and snap['bytes']==auth['bytes']==11843985632
assert sha256_file(p/'manifest.json')==auth['manifest_sha256']==snap['manifest_sha256']==public['manifest_sha256']
assert pull['snapshot_receipt_sha256']==sha256_file(d/'public-snapshot.json')
for k,sha in nfs['quantized'].items():
    assert sha==pull['files'][k]['blob_sha256']==remote['registry_tags'][k]['sha256']
for name in ('evidence/benchmark.json','evidence/holdout-comparison.json','evidence/fixed-generations.json','evidence/format-comparison.json','evidence/cpu-comparison.json','evidence/bf16-acceptance.json','evidence/gguf-lineage.json','evidence/generation-review.json','REPORT.md','USAGE.md'):
    assert sha256_file(n/name)==nfs['files'][name]['sha256']
q=read(n/'evidence/holdout-comparison.json');assert q['all_languages_passed'] and len(q['decisions'])==6
bench=read(n/'evidence/benchmark.json');assert len(bench['rounds'])==4
prior=read(n/'evidence/fixed-generations.json')['outputs']
modes={}
for engine,kinds in [('ollama',['Q8_0','Q4_K_M']),('vllm',['BF16'])]:
    prefix=d/engine
    for fmt in kinds:
        runs=[]
        for i in (1,2):
            folder=d/f'{engine}-{i}'
            life=read(folder/'lifecycle.json');assert life['port_closed'] and life['rc']==0
            assert life['revision']==public['commit']
            with socket.socket() as sock: assert sock.connect_ex(('127.0.0.1',life['port']))!=0
            r=read(folder/(fmt+'-generation.json'))
            assert r['example']['text'] and 0<r['example']['tokens']<=32
            pairs=[(v['prompt'],v['text'],v['tokens']) for v in r['cases']]
            expected=[(v['prompt'],v['text'],v['tokens']) for v in prior[fmt]]
            assert len(pairs)==len(expected)==6 and all(x[0]==y[0] for x,y in zip(pairs,expected))
            runs.append(dict(port=life['port'],rc=life['rc'],port_closed=life['port_closed'],identical_to_nfs=sum(x==y for x,y in zip(pairs,expected)),example=r['example'],pairs=pairs))
            if engine=='ollama':
                show=read(folder/(fmt+'-identity.json'))
                assert show['template']=='{{ .Prompt }}' and show['details']['quantization_level']==fmt
                device,=read(folder/(fmt+'-device.json'))
                assert device['size_vram']>0 and device['model']=='hf.co/'+public['repo']+':'+fmt
                assert device['digest']==pull['files'][fmt]['manifest_sha256']
            else:
                device=read(folder/'device.json');assert device['visible']=='0'
                assert any(int(row.split(',')[0]) in device['own_pids'] and row.split(',')[1].strip()==life['physical_device']['uuid']
                           for row in device['compute_apps'].splitlines())
        assert runs[0]['pairs']==runs[1]['pairs'] and runs[0]['example']==runs[1]['example']
        default_matches=[v['identical_to_nfs'] for v in runs]
        if min(default_matches)<6:
            assert fmt=='BF16'
            folder=d/'vllm-3'
            v=read(folder/'BF16-generation.json')
            assert [(u['prompt'],u['text'],u['tokens']) for u in v['cases']]==expected
            life=read(folder/'lifecycle.json');assert life['rc']==0 and life['port_closed']
        modes[fmt]=dict(default_nfs_matches=default_matches,matched_settings_nfs_matches=6,reload_examples_identical=True,reload_six_identical=True,ports_closed=True)
atomic_json(d/'verified.json',dict(repo=public['repo'],commit=public['commit'],visibility='public',license='apache-2.0',manifest_sha256=snap['manifest_sha256'],nfs_manifest_sha256=digest,formats=modes,proofs={name:sha256_file(d/name) for name in ['commit.json','public-snapshot.json','public-metadata.json','ollama-pull.json']}))
url='https://huggingface.co/'+public['repo']+'/tree/'+public['commit']
rows=read(n/'evidence/format-comparison.json')
lines=['# JANSORI 47K 배포 및 다운로드 검증','',f"사용자 승인에 따라 세 형식을 [단일 공개 저장소의 같은 커밋]({url})에 게시했다. 가중치와 토크나이저에는 Apache-2.0을 적용했다. 배포 commit은 `{public['commit']}`이다. 비로그인으로 18개 파일 {snap['bytes']:,}바이트를 확인했다.",'','## 계보 및 보존','',f"NFS 배포 경로는 `{n}`이며 manifest SHA-256은 `{digest}`다. 원본은 `{nfs['source']}`이다. BF16 패키지 manifest SHA-256은 `{nfs['bf16_candidate_sha256']}`이다. 이 패키지에서 만든 공통 고정밀 GGUF SHA-256은 `{nfs['float_gguf_sha256']}`이다.",'']
for k,sha in nfs['quantized'].items():lines.append(f'- {k} SHA-256 `{sha}`')
lines+=['','## 형식별 실측 및 다운로드 검증','', '| 형식 | GB | 한국어 / 영어 PPL | 로딩 초 | 생성 토큰/초 | GPU 최고 MiB | 기본 실행 NFS 일치, 최초 / 재로딩 |','|---|---:|---|---:|---:|---:|---|']
for t in rows:
 k=t['format'];a,b=modes[k]['default_nfs_matches'];lines.append(f"| {k} | {t['bytes']/1e9:.3f} | {t['ko']['perplexity']:.5f} / {t['en']['perplexity']:.5f} | {t['load_s']:.2f} | {t['tokens_per_s']:.2f} | {t['generation_gpu_peak_mib']:.1f} | {a}/6 · {b}/6 |")
lines+=['','형식별 성능은 첫 순차 회차의 로컬 H200 실측이다. 두 순차 회차 평균은 174.99초, 두 병행 회차 평균은 117.45초였다. 서버 트리 최대 PSS는 순차 3.64 GiB, 병행 4.69 GiB였다. 동시에 측정한 두 GPU 합산 VRAM 최고치는 순차 9.20 GiB, 병행 13.44 GiB였다. 고정 입력을 새 프로세스에서 다시 실행해 각 형식에서 최초·재로딩 출력 6/6 일치와 정상 종료·포트 해제를 확인했다.','', '새 HF 캐시와 Ollama 모델 저장소에서 공개 배포본을 받았다. 일부 HF 전송이 지연되어 같은 commit의 HTTP Range로 BF16 shard의 67,104,768바이트를 보완하고, 새 Ollama 저장소에서 받은 Q8 파일을 재사용했다. 18개 파일 각각의 크기와 SHA-256을 검증했다. GGUF 태그 manifest, 받은 blob, NFS 양자화 파일의 SHA-256이 일치했다.','', '## 실행 및 재현','', '공개 다운로드 검증에 사용한 사용자 명령이다. 각 서버에 GPU 한 장을 노출한다. Ollama CLI의 긴 출력을 중단하려면 Ctrl+C를 사용한다. 출력 상한이 필요한 경우 설정 문서의 API 예제를 쓴다.','', '```bash',f'REV={public["commit"]}','CUDA_VISIBLE_DEVICES=0 vllm serve pathcosmos/JANSORI --revision "$REV" --tokenizer-revision "$REV" --load-format safetensors --dtype bfloat16 --tensor-parallel-size 1 --max-model-len 4096 --host 127.0.0.1','ollama run hf.co/pathcosmos/JANSORI:Q8_0 "한국의 수도는"','ollama run hf.co/pathcosmos/JANSORI:Q4_K_M "한국의 수도는"','```','','[NFS 한 장 실행 및 재설치 절차](../nfs-release-v1/USAGE.md) · [수치·생성·성능 측정](../nfs-release-v1/REPORT.md) · [후속 상세 시험과 세부 설정](TEST_AND_SETTINGS.md) · [배포 검증 영수증](verified.json)','']
if min(modes['BF16']['default_nfs_matches'])<6:
    lines.extend(['## vLLM 실행 설정에 따른 출력 차이','',
        '기본 모델 카드 명령에서는 영어 이야기와 방정식 프롬프트의 출력이 기존 NFS 측정과 달랐다. '
        '기본 명령을 재기동하면 최초 실행의 6개 출력이 모두 재현됐다. 같은 공개 commit에서 기존 NFS 벤치마크의 '
        'eager·attention·배치·KV 캐시·생성 옵션을 함께 적용하면 6개 모두 NFS 측정과 일치했다. '
        '이 대조만으로 개별 옵션 하나를 원인으로 특정할 수 없다. '
        '[기본 실행](vllm-1/BF16-generation.json) · [기존 설정 대조](vllm-3/BF16-generation.json)',''])
(d/'RELEASE_REPORT.md').write_text('\n'.join(lines))
