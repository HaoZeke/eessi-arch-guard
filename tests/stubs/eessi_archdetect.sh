#!/bin/bash
# Test stub. Same argv contract as eessi_archdetect.sh: [-a] cpupath|accelpath.
# CPU candidates come from EESSI_PROC_CPUINFO. Accel calls nvidia-smi.
# This is not the EESSI script.

action=""
if [ "${1:-}" = "-a" ]; then
    action="${2:-}"
else
    action="${1:-}"
fi

if [ -n "${EESSI_TEST_ARCH_LOG:-}" ]; then
    printf '%s\n' "$action" >> "$EESSI_TEST_ARCH_LOG"
fi

cpuinfo="${EESSI_PROC_CPUINFO:-/proc/cpuinfo}"

case "$action" in
cpupath)
    if [ ! -r "$cpuinfo" ]; then
        echo "cannot read cpuinfo" >&2
        exit 1
    fi
    if grep -q '^eessi_arch_fail:' "$cpuinfo"; then
        echo "archdetect forced failure" >&2
        exit 1
    fi
    listed=$(grep -m1 '^eessi_arch_matches:' "$cpuinfo" | cut -d: -f2- | tr -d '[:space:]')
    if [ -n "$listed" ]; then
        printf '%s\n' "$listed"
        exit 0
    fi
    model=$(grep -m1 -i '^model name' "$cpuinfo" | cut -d: -f2-)
    case "$model" in
        *7702*|*zen2*)
            printf '%s\n' "x86_64/amd/zen2:x86_64/generic"
            ;;
        *9654*|*zen4*)
            printf '%s\n' "x86_64/amd/zen4:x86_64/amd/zen3:x86_64/amd/zen2:x86_64/generic"
            ;;
        *9755*|*zen5*)
            printf '%s\n' "x86_64/amd/zen5:x86_64/amd/zen4:x86_64/amd/zen3:x86_64/generic"
            ;;
        *)
            printf '%s\n' "x86_64/generic"
            ;;
    esac
    ;;
accelpath)
    if [ -n "${EESSI_ACCELERATOR_TARGET_OVERRIDE:-}" ]; then
        printf '%s\n' "$EESSI_ACCELERATOR_TARGET_OVERRIDE"
        exit 0
    fi
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        exit 2
    fi
    out=$(nvidia-smi --query-gpu=gpu_name,count,driver_version,compute_cap --format=csv,noheader) || exit 3
    cc=$(printf '%s\n' "$out" | head -n 1 | sed 's/, /,/g' | cut -d, -f4 | tr -d '.')
    if [ -z "$cc" ]; then
        exit 3
    fi
    printf 'accel/nvidia/cc%s\n' "$cc"
    ;;
*)
    echo "usage: eessi_archdetect.sh [-a] cpupath|accelpath" >&2
    exit 1
    ;;
esac
