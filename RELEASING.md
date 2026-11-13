# Releasing

A release is a tag `vX.Y.Z` on `main`. The `release` workflow builds the
static Zig binaries for x86_64 and aarch64, runs the Zig tests and the Python
suite against each binary on its own architecture, checks that the tag, the
binary's `--version` and the Python `__version__` agree, and opens a draft
release with the binaries, `SHA256SUMS` and build provenance attestations.
Release builds are reproducible: the same commit built with Zig 0.17.1 gives
the same sha256.

## Checklist

1. `__version__` in `src/eessi_arch_guard.py`, `version` in `pyproject.toml`,
   the `version` option in `build.zig` and `.version` in `build.zig.zon` name
   the new version.
2. `changelog.d` holds an entry for each change; `CHANGELOG.md` is built from
   it.
3. `COMPAT.md` lists every deliberate difference between the Zig build and
   the Python guard.
4. CI on `main` is green on both architectures, the real-Lmod tests ran (a
   skip counts as a failure), and the root-owned configuration tests ran.
5. Before the binary becomes the default prolog: a shadow run beside the
   Python guard on one partition with identical output, and a second review
   of the validators against the Python patterns.
6. Tag and push: `git tag -a vX.Y.Z -m vX.Y.Z && git push origin vX.Y.Z`.
7. Check the draft release: two binaries, `SHA256SUMS`, attestations
   (`gh attestation verify FILE --repo stackhands/eessi-arch-guard`), and a
   local build of the tag with the same sha256.
8. Publish the draft, then update the EasyBuild recipe's version and
   checksums and test-install it.
