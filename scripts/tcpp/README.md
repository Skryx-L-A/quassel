# transcribe.cpp for measurements

Everything here is for measuring other speech models next to today's
whisper-server. The installed app does not use
any of it. Building and downloading are heavy runs; start them deliberately.

Upstream: <https://github.com/handy-computer/transcribe.cpp>, runtime licensed
MIT, pinned to tag `v0.3.1` (commit `3f32fbcc7bb3246851a0234263438bc3c0fa1cac`,
released 2026-10-04). Model weights carry their own licenses (table below).

## Build

```sh
scripts/tcpp/build_transcribe_cpp.sh                 # auto: metal on Apple Silicon
scripts/tcpp/build_transcribe_cpp.sh --backend cuda  # Linux with nvcc
scripts/tcpp/build_transcribe_cpp.sh --backend vulkan
scripts/tcpp/build_transcribe_cpp.sh --dry-run       # print the commands only
```

The script clones the tag into `~/quassel-bench-data/tcpp/src/`, checks
the commit, builds only `transcribe-cli` and writes
`~/quassel-bench-data/tcpp/bin/transcribe-cli-v0.3.1-<backend>` plus a JSON
file with tag, commit, backend and CMake flags. Nothing is installed
globally. It needs `git` and `cmake`; CUDA needs `nvcc`, Vulkan the Vulkan
SDK (`glslc`). Windows builds follow upstream `docs/build-windows.md` at the
same tag; the open upstream Vulkan crash on Windows/AMD makes Vulkan there a
separate test case.

## Models

GGUF files from the `handy-computer` organisation on Hugging Face. Sizes and
SHA-256 come from the Hugging Face API (`/api/models/<repo>?blobs=true`,
read 2026-10-09). Download into `~/quassel-bench-data/tcpp/models/` and
check the hash before the first run:

```sh
mkdir -p ~/quassel-bench-data/tcpp/models && cd ~/quassel-bench-data/tcpp/models
curl -L -o cohere-transcribe-03-2026-Q8_0.gguf \
  https://huggingface.co/handy-computer/cohere-transcribe-03-2026-gguf/resolve/ab667acedcb5d8d56837ef3ed3568c3ad50dde47/cohere-transcribe-03-2026-Q8_0.gguf
shasum -a 256 cohere-transcribe-03-2026-Q8_0.gguf   # Linux: sha256sum
```

The URLs below are pinned to the repository revision the hashes were read
from, so a later re-upload cannot silently change a measurement.

| Model | Family (`--tcpp-family`) | Quant | Size | Weights license |
|---|---|---|---:|---|
| Cohere Transcribe 03-2026 | `cohere` | Q8_0 | 2.41 GB | Apache-2.0 |
| Cohere Transcribe 03-2026 | `cohere` | Q5_K_M | 1.77 GB | Apache-2.0 |
| Qwen3-ASR 1.7B | `qwen3-asr` | Q8_0 | 2.19 GB | Apache-2.0 |
| Qwen3-ASR 1.7B | `qwen3-asr` | Q5_K_M | 1.52 GB | Apache-2.0 |
| Canary 1B v2 | `canary` | Q8_0 | 1.14 GB | CC-BY-4.0 |
| Canary 1B v2 | `canary` | Q5_K_M | 0.84 GB | CC-BY-4.0 |
| Parakeet TDT 0.6B v3 | `parakeet` | Q8_0 | 0.74 GB | CC-BY-4.0 |
| Parakeet TDT 0.6B v3 | `parakeet` | Q5_K_M | 0.55 GB | CC-BY-4.0 |

Download URLs and SHA-256:

- `cohere-transcribe-03-2026-Q8_0.gguf` (2410655232 bytes)
  `https://huggingface.co/handy-computer/cohere-transcribe-03-2026-gguf/resolve/ab667acedcb5d8d56837ef3ed3568c3ad50dde47/cohere-transcribe-03-2026-Q8_0.gguf`
  `931916663432fd895423a4291a8400221802b288967ca2d435fc5e3141c9e71e`
- `cohere-transcribe-03-2026-Q5_K_M.gguf` (1770270208 bytes)
  `https://huggingface.co/handy-computer/cohere-transcribe-03-2026-gguf/resolve/ab667acedcb5d8d56837ef3ed3568c3ad50dde47/cohere-transcribe-03-2026-Q5_K_M.gguf`
  `14d02f1ad6dd77b3a60f82639879012c3adb4fe25c50a5a47a2c4c661daf1558`
- `Qwen3-ASR-1.7B-Q8_0.gguf` (2185030624 bytes)
  `https://huggingface.co/handy-computer/Qwen3-ASR-1.7B-gguf/resolve/3555bd238a8572bbace3ebf60d23b036dc0a5dbe/Qwen3-ASR-1.7B-Q8_0.gguf`
  `9a0d81792dfea2d5f278b8a63deb3ea6e02139ce42c2301f32ea19c4f77526b7`
- `Qwen3-ASR-1.7B-Q5_K_M.gguf` (1517290464 bytes)
  `https://huggingface.co/handy-computer/Qwen3-ASR-1.7B-gguf/resolve/3555bd238a8572bbace3ebf60d23b036dc0a5dbe/Qwen3-ASR-1.7B-Q5_K_M.gguf`
  `034c557fe92ff8fcd9a9c041cbdaad347be0a86a58d3a348f63cf3f0180879d0`
- `canary-1b-v2-Q8_0.gguf` (1144290016 bytes)
  `https://huggingface.co/handy-computer/canary-1b-v2-gguf/resolve/e2d8e6d7f2accc1259dc5497b517b4083047e44b/canary-1b-v2-Q8_0.gguf`
  `224f83d1bc487b3303b495a7d6874912fdece93de19d1a04b550829c30a5d289`
- `canary-1b-v2-Q5_K_M.gguf` (836664032 bytes)
  `https://huggingface.co/handy-computer/canary-1b-v2-gguf/resolve/e2d8e6d7f2accc1259dc5497b517b4083047e44b/canary-1b-v2-Q5_K_M.gguf`
  `9c3a893c93795438baf9b4b1c853c39b60316c3a0d259a3ba6e284712f5ddb71`
- `parakeet-tdt-0.6b-v3-Q8_0.gguf` (739508576 bytes)
  `https://huggingface.co/handy-computer/parakeet-tdt-0.6b-v3-gguf/resolve/90f082450fcbacdb54e5900c44ef697c9ea59622/parakeet-tdt-0.6b-v3-Q8_0.gguf`
  `5859f77944efcd8eafa23a6350731960b2b55b2203df51f319665c807d802cc7`
- `parakeet-tdt-0.6b-v3-Q5_K_M.gguf` (548946272 bytes)
  `https://huggingface.co/handy-computer/parakeet-tdt-0.6b-v3-gguf/resolve/90f082450fcbacdb54e5900c44ef697c9ea59622/parakeet-tdt-0.6b-v3-Q5_K_M.gguf`
  `cc722e76adc1a629fc0b2535de879d99b8160d07ad4c0215e2ca7d7ea0ae4b8f`

## Measuring

```sh
python scripts/bench/run_bench.py --backend transcribe-cpp \
  --transcribe-cpp ~/quassel-bench-data/tcpp/bin/transcribe-cli-v0.3.1-metal \
  --tcpp-model ~/quassel-bench-data/tcpp/models/cohere-transcribe-03-2026-Q8_0.gguf \
  --tcpp-family cohere
```

The report names the model file, family and quantisation.

## How the adapter talks to transcribe-cli

`quassel/backends/transcribe_cpp.py` (`TranscribeCppBackend`):

- **One process per clip.** transcribe-cli has no server or stdin mode; its
  batch list is read completely before the first run. Every clip therefore
  pays the model load, and the measured latency includes it. The Python
  binding variant (below) keeps the model loaded instead.
- It calls `transcribe-cli -m MODEL --batch LIST --batch-jsonl -q --backend B`
  with a one-line list, because only the batch mode prints machine-readable
  JSON (text, segment times, error status).
- **Language.** Cohere and Canary need a language and get `de` when Quassel
  is set to `mixed` or `auto`; the result carries a note. Nemotron gets a
  locale (`de-DE`). Qwen3-ASR, Whisper and Parakeet v3 detect the language
  themselves. Voxtral Realtime never gets one.
- **Dictionary.** Only Whisper and Qwen3-ASR accept a vocabulary
  (upstream `docs/prompting.md`). It goes through a private temporary file.
  Context text from the dictation is never passed to the CLI: it would be
  visible in the process list.
- **Limits.** Cohere accepts about 400 s per call; longer clips are refused
  before a process starts.
- **Word times.** The JSON output carries segment times only; word times
  exist only in the human-readable output and through the Python binding.

## Python binding (second path)

`quassel/backends/transcribe_cpp_py.py` (`TranscribeCppPythonBackend`) uses
`transcribe-cpp` 0.3.1 from PyPI with `transcribe-cpp-native` 0.3.1. The
native package ships a `macosx_11_0_arm64` wheel with Metal, Linux and
Windows wheels with CPU and Vulkan, and CUDA 12 as
`transcribe-cpp-native-cu12`. Not installed by default; for a measurement,
install both into a separate virtual environment. Without them the backend
reports itself as unavailable.
