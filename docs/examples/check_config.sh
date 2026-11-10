#!/bin/bash
# Exercise the root-owned config beside a copy of the prolog.
# Prints a transcript with the temporary directory rewritten as $WORK.
# Removes the temporary directory, including root-owned files, on exit.
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
parent=$(mktemp -d /tmp/eessi-arch-guard-conf.XXXXXX)
cleanup() {
    sudo -n rm -rf "$parent"
}
trap cleanup EXIT

run_guard() {
    local work=$1
    local stdout stderr code
    stdout=$(mktemp)
    stderr=$(mktemp)
    set +e
    env -i PATH=/usr/bin:/bin LC_ALL=C HOME=/tmp "$work/eessi-arch-guard" >"$stdout" 2>"$stderr"
    code=$?
    set -e
    python3 - "$work" "$code" "$stdout" "$stderr" <<'PY'
import pathlib, sys
work, code, out_path, err_path = sys.argv[1:]
stdout = pathlib.Path(out_path).read_bytes().decode()
stderr = pathlib.Path(err_path).read_bytes().decode()
stdout = stdout.replace(work, "$WORK")
stderr = stderr.replace(work, "$WORK")
print("exit: %s" % code)
print("--- stdout ---")
sys.stdout.write(stdout if stdout else "(empty)\n")
print("--- stderr ---")
sys.stdout.write(stderr if stderr else "(empty)\n")
PY
    rm -f "$stdout" "$stderr"
    echo "$code" >"$parent/last-exit"
    return 0
}

expect_exit() {
    local want=$1
    local got=$2
    if [ "$got" -ne "$want" ]; then
        echo "expected exit $want, got $got" >&2
        exit 1
    fi
}

prepare() {
    local name=$1
    local work=$parent/$name
    mkdir -p "$work"
    cp "$root/src/eessi_arch_guard.py" "$work/eessi-arch-guard"
    chmod 755 "$work/eessi-arch-guard"
    printf '%s\n' '#!/bin/sh' "printf '%s\n' 'export SITE_PROLOG=1'" >"$work/site.sh"
    chmod 755 "$work/site.sh"
    printf '%s\n' "$work"
}

echo "case: user-owned"
work=$(prepare user-owned)
printf 'chain = %s\n' "$work/site.sh" >"$work/eessi-arch-guard.conf"
run_guard "$work"
expect_exit 0 "$(cat "$parent/last-exit")"

echo "case: root-owned-chain"
work=$(prepare root-owned)
# A non-UTF-8 byte in a comment leaves the chain key in force.
printf '# \377\nchain = %s\ntimeout = 15\n' "$work/site.sh" >"$work/eessi-arch-guard.conf"
sudo -n chown root:root "$work/eessi-arch-guard.conf"
sudo -n chmod 644 "$work/eessi-arch-guard.conf"
python3 - "$work/eessi-arch-guard.conf" <<'PY'
import pathlib, sys
blob = pathlib.Path(sys.argv[1]).read_bytes()
if b"\377" not in blob.splitlines()[0]:
    raise SystemExit("comment byte missing")
print("comment_has_0xff: yes")
PY
run_guard "$work"
expect_exit 0 "$(cat "$parent/last-exit")"

echo "case: chain-fails"
work=$(prepare chain-fails)
printf '%s\n' '#!/bin/sh' "echo fail-prolog >&2" "exit 7" >"$work/site.sh"
chmod 755 "$work/site.sh"
printf 'chain = %s\n' "$work/site.sh" >"$work/eessi-arch-guard.conf"
sudo -n chown root:root "$work/eessi-arch-guard.conf"
sudo -n chmod 644 "$work/eessi-arch-guard.conf"
run_guard "$work"
expect_exit 7 "$(cat "$parent/last-exit")"

echo "case: bad-timeout"
work=$(prepare bad-timeout)
printf 'chain = %s\ntimeout = 0\n' "$work/site.sh" >"$work/eessi-arch-guard.conf"
sudo -n chown root:root "$work/eessi-arch-guard.conf"
sudo -n chmod 644 "$work/eessi-arch-guard.conf"
run_guard "$work"
expect_exit 0 "$(cat "$parent/last-exit")"

echo "case: non-utf8-value"
work=$(prepare non-utf8)
printf 'other = \377\nchain = %s\n' "$work/site.sh" >"$work/eessi-arch-guard.conf"
sudo -n chown root:root "$work/eessi-arch-guard.conf"
sudo -n chmod 644 "$work/eessi-arch-guard.conf"
run_guard "$work"
expect_exit 0 "$(cat "$parent/last-exit")"

echo "case: symlink"
work=$(prepare symlink)
printf 'chain = %s\n' "$work/site.sh" >"$work/real.conf"
sudo -n chown root:root "$work/real.conf"
sudo -n chmod 644 "$work/real.conf"
ln -s real.conf "$work/eessi-arch-guard.conf"
run_guard "$work"
expect_exit 0 "$(cat "$parent/last-exit")"

run_any() {
    local stdout stderr code
    stdout=$(mktemp)
    stderr=$(mktemp)
    set +e
    env -i PATH=/usr/bin:/bin LC_ALL=C HOME=/tmp "$@" >"$stdout" 2>"$stderr"
    code=$?
    set -e
    python3 - "$1" "$code" "$stdout" "$stderr" "$parent" <<'PY'
import pathlib, sys
shown, code, out_path, err_path, parent = sys.argv[1:]
stdout = pathlib.Path(out_path).read_bytes().decode().replace(parent, "$WORK")
stderr = pathlib.Path(err_path).read_bytes().decode().replace(parent, "$WORK")
print("command: %s" % shown.replace(parent, "$WORK"))
print("exit: %s" % code)
print("--- stdout ---")
sys.stdout.write(stdout if stdout else "(empty)\n")
print("--- stderr ---")
sys.stdout.write(stderr if stderr else "(empty)\n")
PY
    rm -f "$stdout" "$stderr"
    echo "$code" >"$parent/last-exit"
    return 0
}

echo "case: wrapper"
work=$(prepare wrapper)
printf '%s\n' '#!/bin/sh' "printf '%s\n' 'export FROM_SITE=1'" >"$work/site.sh"
chmod 755 "$work/site.sh"
cat >"$work/wrapper.sh" <<EOF
#!/bin/sh
$work/site.sh || exit \$?
exec /usr/bin/python3 -I $work/eessi-arch-guard
EOF
chmod 755 "$work/wrapper.sh"
run_any "$work/wrapper.sh"
expect_exit 0 "$(cat "$parent/last-exit")"

echo "case: wrapper-site-fails"
work=$(prepare wrapper-fails)
printf '%s\n' '#!/bin/sh' "echo site-failed >&2" "exit 9" >"$work/site.sh"
chmod 755 "$work/site.sh"
cat >"$work/wrapper.sh" <<EOF
#!/bin/sh
$work/site.sh || exit \$?
exec /usr/bin/python3 -I $work/eessi-arch-guard
EOF
chmod 755 "$work/wrapper.sh"
run_any "$work/wrapper.sh"
expect_exit 9 "$(cat "$parent/last-exit")"

echo "case: group-writable"
work=$(prepare group-writable)
printf 'chain = %s\n' "$work/site.sh" >"$work/eessi-arch-guard.conf"
sudo -n chown root:root "$work/eessi-arch-guard.conf"
sudo -n chmod 664 "$work/eessi-arch-guard.conf"
run_guard "$work"
expect_exit 0 "$(cat "$parent/last-exit")"
