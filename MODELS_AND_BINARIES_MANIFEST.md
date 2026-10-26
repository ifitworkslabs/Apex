# LocalAI Artifact & Binary Manifest

This file documents the binary dependencies, virtual disk images, and model weights residing in this directory that are excluded from version control due to GitHub's file size limits and best practices for repo performance.

---

## 1. Large Language Models (`models/`)

Location: `d:\LocalAI\models\`

| File Name | Size | Description |
| :--- | :--- | :--- |
| `Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf` | ~16.45 GB | Qwen3 Coder 30B GGUF quantized model (Q4_K_XL) |
| `Qwen3.5-27B-UD-Q3_K_XL.gguf` | ~13.45 GB | Qwen3.5 27B GGUF quantized model (Q3_K_XL) |
| `.cache/huggingface/` | ~384 B | HuggingFace download cache metadata |

---

## 2. Docker WSL Virtual Disks (`DockerWSL/`)

Location: `d:\LocalAI\DockerWSL\`

| File / Subfolder | Size | Description |
| :--- | :--- | :--- |
| `DockerDesktopWSL\disk\docker_data.vhdx` | ~65.66 GB | Docker Desktop WSL2 root storage virtual hard disk |
| `DockerDesktopWSL\main\ext4.vhdx` | ~96 MB | Docker Desktop WSL2 distribution system disk |

---

## 3. Llama.cpp CUDA Runtime & Binaries (`llama/`)

Location: `d:\LocalAI\llama\`
Total Size: ~1.13 GB

- Contains compiled `llama.cpp` Windows binaries and CUDA 12 support DLLs:
  - `llama-server.exe`, `llama-cli.exe`, `llama-bench.exe`, etc.
  - CUDA accelerated backends: `cublas64_12.dll`, `cublasLt64_12.dll`, `cudart64_12.dll`, `ggml-cuda.dll`, etc.

---

## 4. Reverse Engineering & Decompilation Tools (`ghidra/`)

Location: `d:\LocalAI\ghidra\`
Total Size: ~890 MB

- Ghidra 12.2 PUBLIC installation and extensions:
  - `docs/GhidraAPI_javadoc.zip` (58.75 MB)
  - `Extensions/Ghidra/ghidra_12.2_PUBLIC_20261016_Jython.zip` (53.72 MB)
  - Java runtime dependencies and Ghidra decompilation framework.

---

## 5. Java Development Kits (`jdk21/`, `jdk25/`)

- `jdk21/` (~328 MB) - OpenJDK 21 portable installation
- `jdk25/` (~368 MB) - OpenJDK 25 portable installation
