#!/usr/bin/env bash
# Day 1 环境自检 —— 在服务器上运行，输出一份可归档的环境快照
#
# 用法：
#   bash scripts/day01_env_check.sh
# 或把输出重定向到文件：
#   bash scripts/day01_env_check.sh 2>&1 | tee results/day01_env_check.txt
#
# 设计原则：
#   - 只读，不改任何东西（除了 mkdir results）
#   - 每条命令失败都不中断，继续往下跑，最后汇总
#   - 输出用 === 分节，方便人工核对，也方便后续 grep

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p results

hr() { printf '\n=== %s ===\n' "$1"; }
run() {
  local label="$1"; shift
  printf '$ %s\n' "$*"
  if output=$("$@" 2>&1); then
    printf '%s\n' "$output"
  else
    printf '[FAILED exit=%d]\n%s\n' "$?" "$output"
  fi
}

hr "0. meta"
printf 'captured_at: %s\n' "$(date -Iseconds)"
printf 'hostname:    %s\n' "$(hostname)"
printf 'user:        %s\n' "$(whoami)"
printf 'repo_root:   %s\n' "$ROOT"
printf 'git_commit:  %s\n' "$(git rev-parse HEAD 2>/dev/null || echo 'not a git repo')"
printf 'git_status:  %s\n' "$(git status --porcelain 2>/dev/null | wc -l) modified files"

hr "1. OS / kernel"
run "os-release" bash -c 'cat /etc/os-release 2>/dev/null | head -4'
run "uname" uname -a
run "arch" uname -m
run "mem" bash -c 'free -h 2>/dev/null || true'
run "disk" bash -c 'df -h . 2>/dev/null | tail -2'

hr "2. CPU"
run "nproc" nproc
run "cpu_model" bash -c "grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2 || true"

hr "3. GPU"
if command -v nvidia-smi >/dev/null 2>&1; then
  run "nvidia-smi" nvidia-smi
  run "nvidia-smi-query" nvidia-smi \
    --query-gpu=index,name,memory.total,memory.used,memory.free,driver_version,compute_cap \
    --format=csv
  # 16G 标称值可能因 ECC 或多卡切分而不足，这里单独看实际可用
  run "cuda_version" bash -c 'nvidia-smi | grep -i "CUDA Version" || true'
else
  printf 'nvidia-smi NOT FOUND -> 无 NVIDIA GPU 或驱动未装\n'
fi

hr "4. Python"
run "python3_version" python3 -V
run "python_alias" bash -c 'command -v python || echo "no python alias"'
run "conda" bash -c 'command -v conda && conda --version || echo "no conda"'
run "conda_envs" bash -c 'conda env list 2>/dev/null || true'
run "pip" bash -c 'python3 -m pip --version 2>&1 || true'

hr "5. Docker"
if command -v docker >/dev/null 2>&1; then
  run "docker_version" docker --version
  run "compose_version" docker compose version
  run "docker_info" bash -c 'docker info 2>&1 | head -20'
  run "docker_ps" docker ps -a
  # 当前用户是否在 docker 组（决定要不要 sudo）
  run "docker_group" bash -c "id -nG | tr ' ' '\n' | grep -x docker || echo 'user NOT in docker group -> need sudo'"
else
  printf 'docker NOT FOUND\n'
fi

hr "6. Java (Neo4j 社区版 tarball 需要 JRE 17/21)"
run "java" bash -c 'command -v java && java -version 2>&1 || echo "no java"'

hr "7. 网络（只测连通性，不下大文件）"
check_url() {
  local url="$1" name="$2"
  printf '$ curl -sS -o /dev/null -w "%%{http_code}" --max-time 10 %s\n' "$url"
  code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 10 "$url" 2>&1) || code="curl-error"
  printf '%s -> %s\n' "$name" "$code"
}
run "curl_version" bash -c 'curl --version | head -1'
check_url "https://huggingface.co" "huggingface"
check_url "https://www.modelscope.cn" "modelscope"
check_url "https://pypi.org/simple/" "pypi"

hr "8. 仓库文件盘点"
run "tree_top" bash -c 'ls -la'
run "csv_rows" bash -c 'for f in data/C9/cypher/*.csv; do printf "%-40s %s lines\n" "$f" "$(wc -l < "$f")"; done'
run "py_loc" bash -c 'find . -name "*.py" -not -path "./.git/*" | xargs wc -l 2>/dev/null | tail -12'

hr "9. 目录约定检查"
for d in results docs scripts evaluation tests data/benchmark; do
  if [ -d "$d" ]; then printf '%-20s EXISTS\n' "$d"; else printf '%-20s MISSING (later)\n' "$d"; fi
done

hr "done"
printf '把以上完整输出贴回给导师（或保存为 results/day01_env_check.txt）\n'
