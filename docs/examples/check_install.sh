#!/bin/bash
# Install the prolog file and the console script, print the stable lines, and remove both.
# Refuses to replace an existing /usr/local/sbin/eessi-arch-guard.
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$root"

dest=/usr/local/sbin/eessi-arch-guard
if [ -e "$dest" ]; then
    echo "refusing to replace $dest" >&2
    exit 1
fi

echo "+ sudo install -m 755 src/eessi_arch_guard.py $dest"
sudo -n install -m 755 src/eessi_arch_guard.py "$dest"
cleanup_dest() {
    sudo -n rm -f "$dest"
}
trap cleanup_dest EXIT

echo "+ head -n 1 $dest"
head -n 1 "$dest"
echo "+ $dest --version"
"$dest" --version

venv=$(mktemp -d /tmp/eessi-arch-guard-venv.XXXXXX)
cleanup_all() {
    sudo -n rm -f "$dest"
    rm -rf "$venv"
}
trap cleanup_all EXIT

echo "+ python3 -m venv <venv>"
python3 -m venv "$venv"
echo "+ <venv>/bin/python -m pip install ."
"$venv/bin/python" -m pip install --disable-pip-version-check --quiet "$root"
echo "+ <venv>/bin/python -c 'import importlib.metadata; print(importlib.metadata.version(\"eessi-arch-guard\"))'"
"$venv/bin/python" -c 'import importlib.metadata; print(importlib.metadata.version("eessi-arch-guard"))'
console="$venv/bin/eessi-arch-guard"
echo "console: <venv>/bin/eessi-arch-guard"
if grep -q 'from eessi_arch_guard import main' "$console"; then
    echo "console import: from eessi_arch_guard import main"
else
    echo "console script is missing the main import" >&2
    exit 1
fi
if grep -q -- '-I' "$console"; then
    echo "console -I flag: present" >&2
    exit 1
fi
echo "console -I flag: absent"
echo "+ <venv>/bin/eessi-arch-guard --version"
"$venv/bin/eessi-arch-guard" --version
