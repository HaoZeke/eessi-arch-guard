#!/bin/bash
# Test stub. Same argv contract as eessi_archdetect.sh: [-a] cpupath|accelpath.
# CPU candidates come from EESSI_PROC_CPUINFO. Accel calls nvidia-smi.
# This is not the EESSI script.
# An eessi_arch_matches line, or a zen model-name token, is the short path
# the other tests use. Otherwise the x86 flag list from eessi_arch_x86.spec
# is applied. That list does not require waitpkg, matching archdetect.
# The guard is what drops a march when the flag is missing.

x86_consider() {
    local chain="$1" path="$2" vendor="$3" need="$4" flags="$5"
    shift 5
    local flag
    if [ "${vendor}x" != "${need}x" ]; then
        printf '%s\n' "$chain"
        return 0
    fi
    for flag in "$@"; do
        case " ${flags} " in
            *" ${flag} "*) ;;
            *)
                printf '%s\n' "$chain"
                return 0
                ;;
        esac
    done
    printf '%s\n' "${path}:${chain}"
}

x86_flag_cpupath() {
    local info="$1"
    local vendor flags chain family model
    vendor=$(grep -m1 -i '^vendor_id[[:space:]]*:' "$info" | cut -d: -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    flags=$(grep -m1 -i '^flags[[:space:]]*:' "$info" | cut -d: -f2-)
    chain="x86_64/generic"
    chain=$(x86_consider "$chain" "x86_64/intel/haswell" "$vendor" GenuineIntel "$flags" avx2 fma)
    chain=$(x86_consider "$chain" "x86_64/intel/skylake_avx512" "$vendor" GenuineIntel "$flags" avx2 fma avx512f avx512bw avx512cd avx512dq avx512vl)
    chain=$(x86_consider "$chain" "x86_64/intel/cascadelake" "$vendor" GenuineIntel "$flags" avx2 fma avx512f avx512bw avx512cd avx512dq avx512vl avx512_vnni)
    chain=$(x86_consider "$chain" "x86_64/intel/icelake" "$vendor" GenuineIntel "$flags" avx2 fma avx512f avx512bw avx512cd avx512dq avx512vl avx512_vnni avx512_vbmi2)
    chain=$(x86_consider "$chain" "x86_64/intel/sapphirerapids" "$vendor" GenuineIntel "$flags" avx2 fma avx512f avx512bw avx512cd avx512dq avx512vl avx512_bf16 amx_tile)
    chain=$(x86_consider "$chain" "x86_64/amd/zen2" "$vendor" AuthenticAMD "$flags" avx2 fma)
    chain=$(x86_consider "$chain" "x86_64/amd/zen3" "$vendor" AuthenticAMD "$flags" avx2 fma vaes)
    chain=$(x86_consider "$chain" "x86_64/amd/zen4" "$vendor" AuthenticAMD "$flags" avx2 fma vaes avx512f avx512ifma)
    chain=$(x86_consider "$chain" "x86_64/amd/zen5" "$vendor" AuthenticAMD "$flags" avx2 fma vaes avx512f avx512ifma avx512_vp2intersect avx_vnni movdiri)
    # Same model-number upgrade as archdetect. Hidden Granite Rapids
    # features are not in cpuinfo, so the flag match stops at sapphirerapids.
    if [ "$chain" != "${chain#x86_64/intel/sapphirerapids:}" ] || [ "$chain" = "x86_64/intel/sapphirerapids" ]; then
        family=$(grep -m1 -i '^cpu family[[:space:]]*:' "$info" | cut -d: -f2- | tr -d '[:space:]')
        model=$(grep -m1 -i '^model[[:space:]]*:' "$info" | cut -d: -f2- | tr -d '[:space:]')
        if [ "$family" = "6" ] && { [ "$model" = "173" ] || [ "$model" = "174" ]; }; then
            chain="x86_64/intel/graniterapids:${chain}"
        fi
    fi
    printf '%s\n' "$chain"
}

action=""
if [ "${1:-}" = "-a" ]; then
    action="${2:-}"
else
    action="${1:-}"
fi

if [ -n "${EESSI_TEST_ARCH_LOG:-}" ]; then
    printf '%s\n' "$action" >> "$EESSI_TEST_ARCH_LOG"
fi

if [ -n "${EESSI_TEST_SETSID_HOLD:-}" ]; then
    # Grandchild in its own session inherits this stdout pipe.
    setsid bash -c "sleep ${EESSI_TEST_SETSID_HOLD}" &
    if [ -n "${EESSI_TEST_HANG_PID:-}" ]; then
        printf '%s\n' "$!" > "$EESSI_TEST_HANG_PID"
    fi
    sleep "${EESSI_TEST_SETSID_HOLD}"
    exit 0
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
            x86_flag_cpupath "$cpuinfo"
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
