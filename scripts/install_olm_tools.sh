#!/usr/bin/env bash
set -Eeuo pipefail
DEST=${1:?Usage: install_olm_tools.sh DESTINATION}
mkdir -p "$DEST"
DEST=$(cd "$DEST" && pwd)
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$(uname -m)" in
  x86_64) ARCH=amd64 ;;
  arm64|aarch64) ARCH=arm64 ;;
  *) echo 'Unsupported tool architecture' >&2; exit 1 ;;
esac
SDK_VERSION=v1.42.3
OPM_VERSION=v1.74.0
for TOOL in sdk opm; do
  if [ "$TOOL" = sdk ]; then
    FILE="operator-sdk_${OS}_${ARCH}"
    REPO=operator-framework/operator-sdk
    VERSION=$SDK_VERSION
    BINARY=operator-sdk
  else
    FILE="${OS}-${ARCH}-opm"
    REPO=operator-framework/operator-registry
    VERSION=$OPM_VERSION
    BINARY=opm
  fi
  curl -fsSL "https://github.com/$REPO/releases/download/$VERSION/$FILE" -o "$DEST/$FILE"
  EXPECTED=$(curl -fsSL "https://github.com/$REPO/releases/download/$VERSION/checksums.txt" | awk -v file="$FILE" '$2 == file {print $1}')
  ACTUAL=$(shasum -a 256 "$DEST/$FILE" | awk '{print $1}')
  test -n "$EXPECTED" && test "$EXPECTED" = "$ACTUAL"
  chmod +x "$DEST/$FILE"
  mv "$DEST/$FILE" "$DEST/$BINARY"
done
