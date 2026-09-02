#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: build_model_image.sh --context DIR --image NAME [--push | --oci FILE | --load]
       [--platforms LIST] [--builder NAME] [--build-arg KEY=VALUE ...]

Builds for linux/amd64 and linux/arm64 by default. Registry authentication is
provided to Docker (for example, with `docker login`), never accepted here.
EOF
}

context= image= output= platforms=linux/amd64,linux/arm64 builder=
args=()
while (($#)); do
  case "$1" in
    --context) context=${2:?missing context}; shift 2 ;;
    --image) image=${2:?missing image}; shift 2 ;;
    --platforms) platforms=${2:?missing platforms}; shift 2 ;;
    --builder) builder=${2:?missing builder}; shift 2 ;;
    --push) [[ -z $output ]] || { echo "choose one output mode" >&2; exit 2; }; output=--push; shift ;;
    --load) [[ -z $output ]] || { echo "choose one output mode" >&2; exit 2; }; output=--load; shift ;;
    --oci) [[ -z $output ]] || { echo "choose one output mode" >&2; exit 2; }; output="--output=type=oci,dest=${2:?missing OCI tar path}"; shift 2 ;;
    --build-arg) args+=(--build-arg "${2:?missing build argument}"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n $context && -n $image ]] || { usage >&2; exit 2; }
[[ -d $context && -f $context/Dockerfile ]] || { echo "context must contain a Dockerfile: $context" >&2; exit 2; }
[[ -n $output ]] || { echo "choose --push, --oci FILE, or --load" >&2; exit 2; }
if [[ $output == --load && $platforms == *,* ]]; then
  echo "--load supports exactly one platform; use --platforms linux/amd64 (or linux/arm64)" >&2
  exit 2
fi
if [[ $output == --output=type=oci,dest=* ]]; then
  destination=${output#--output=type=oci,dest=}
  mkdir -p "$(dirname -- "$destination")"
fi
command -v docker >/dev/null || { echo "docker is required" >&2; exit 127; }
cmd=(docker buildx build --platform "$platforms" --tag "$image")
[[ -z $builder ]] || cmd+=(--builder "$builder")
cmd+=("${args[@]}" "$output" "$context")
exec "${cmd[@]}"
