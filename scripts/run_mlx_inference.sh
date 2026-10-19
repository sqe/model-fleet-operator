#!/usr/bin/env bash
set -euo pipefail

model=${MLX_MODEL:-mlx-community/Qwen3.8-27B-4bit}
port=${MLX_PORT:-8081}
server=${MLX_SERVER:-}
minimum_disk_gib=${MLX_MINIMUM_DISK_GIB:-20}

if [[ $(uname -s) != Darwin || $(uname -m) != arm64 ]]; then
  echo "MLX inference requires an Apple Silicon macOS host; use a GPU InferenceService on Kubernetes." >&2
  exit 1
fi

if [[ -z $server ]]; then
  for candidate in .venv-mlx/bin/mlx_lm.server mlx_lm.server; do
    if command -v "$candidate" >/dev/null 2>&1; then
      server=$candidate
      break
    fi
  done
fi
if [[ -z $server ]]; then
  cat >&2 <<'EOF'
mlx-lm was not found. Install it in an isolated Python 3.12 environment:
  python3.12 -m venv .venv-mlx
  .venv-mlx/bin/pip install --upgrade mlx-lm
EOF
  exit 1
fi

available_kib=$(df -Pk "$HOME" | awk 'NR == 2 {print $4}')
if (( available_kib < minimum_disk_gib * 1024 * 1024 )); then
  printf '%s requires at least %d GiB free disk; %d GiB is available.\n' \
    "$model" "$minimum_disk_gib" "$((available_kib / 1024 / 1024))" >&2
  exit 1
fi

memory_gib=$(($(sysctl -n hw.memsize) / 1024 / 1024 / 1024))
if (( memory_gib < 24 )); then
  printf 'Warning: %d GiB unified memory is unlikely to provide useful headroom for %s.\n' \
    "$memory_gib" "$model" >&2
fi

echo "Starting $model with MLX on 0.0.0.0:$port"
exec "$server" \
  --model "$model" \
  --host 0.0.0.0 \
  --port "$port" \
  --max-tokens "${MLX_MAX_TOKENS:-256}" \
  --prompt-cache-size "${MLX_PROMPT_CACHE_SIZE:-4}" \
  --prompt-cache-bytes "${MLX_PROMPT_CACHE_BYTES:-4GB}"
