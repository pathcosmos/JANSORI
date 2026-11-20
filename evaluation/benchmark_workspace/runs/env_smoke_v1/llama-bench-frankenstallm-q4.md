  Device 0: NVIDIA H200, compute capability 9.0, VMM: yes, VRAM: 143155 MiB
| model                          |       size |     params | backend    | ngl |            test |                  t/s |
| ------------------------------ | ---------: | ---------: | ---------- | --: | --------------: | -------------------: |
| llama 3B Q4_K - Medium         |   1.72 GiB |     3.02 B | CUDA       |  99 |           pp512 |   18200.41 ± 1446.40 |
| llama 3B Q4_K - Medium         |   1.72 GiB |     3.02 B | CUDA       |  99 |           tg128 |        451.26 ± 1.22 |
build: f9448cd (1)
