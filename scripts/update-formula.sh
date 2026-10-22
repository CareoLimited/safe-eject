#!/bin/bash
# Points the formula at a tagged release and fills in its checksum.
# Usage: scripts/update-formula.sh 1.0.0
set -euo pipefail

version="${1:?usage: $0 <version, e.g. 1.0.0>}"
formula="$(cd "$(dirname "$0")/.." && pwd)/Formula/safe-eject.rb"
url="https://github.com/CareoLimited/safe-eject/archive/refs/tags/v${version}.tar.gz"

sha="$(curl -fsSL "$url" | shasum -a 256 | cut -d ' ' -f 1)"
sed -i '' -E "s|^  url \".*\"|  url \"${url}\"|; s|^  sha256 \".*\"|  sha256 \"${sha}\"|" "$formula"
echo "Formula now points to v${version} (sha256 ${sha})."
