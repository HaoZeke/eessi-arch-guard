## [0.1.0] - 2026-11-05

### Features

- The decision stops at one deadline, a killed helper loses its temporary directory, and config `timeout` applies with `--chain` and `--shell`.
- The root-owned config file is read as a map of settings.

### Bugfixes

- A GCC march that includes waitpkg is skipped when the CPU flag is missing, and the next installed archdetect candidate is loaded.
- A config value that is not UTF-8 is kept with surrogateescape and the other keys still apply.
- An interrupted cache write removes its temporary file.
- Export values keep their bytes, a failed write exits 0, and each prolog line stays within 4094 bytes.
- The fake Lmod writes each script line as the environment's own bytes.
- The package metadata keeps the MIT license expression and omits the license classifier that current setuptools rejects.
- The restore helper is `python3 -I -X utf8` and carries each name and value as those same bytes.
- The sdist includes CHANGELOG.md, DESIGN.md, and the test files the default manifest left out.

### Docs

- An Intel A100 node uses the first archdetect candidate whose software directory exists.
