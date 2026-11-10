# Development

The suite uses the standard library and the stubs under `tests/`. Those stubs stand in for the prefix, archdetect, and `nvidia-smi`. `tests/test_real_lmod.py` runs one reload through `/usr/share/lmod/lmod/libexec/lmod` when that file exists and is skipped otherwise.

```console
$ python3 -m pip install ruff
$ ruff check src tests
$ python3 -m unittest discover -s tests -v
```

On this machine `ruff check src tests docs/examples` exits 0, and the suite reports `OK (skipped=1)` because the Lmod libexec path is absent.

`python3 tests/measure_noop.py` prints the host, the Python version, and cold, warm, and baseline percentile timings for a same-target run. The numbers belong to the machine that runs it.

Build the HTML documentation from a virtualenv that has `docs/requirements.txt` installed:

```console
$ python3 -m pip install -r docs/requirements.txt
$ make -C docs html
```

`make -C docs html` runs `python3 -m sphinx -W --keep-going -b html docs docs/_build/html` via the Makefile, and treats warnings as errors. The pages are written to `docs/_build/html`.

The documentation examples assert the lines quoted in the tutorial and the how-tos:

```console
$ python3 docs/examples/walkthrough.py mismatch
$ python3 docs/examples/walkthrough.py again
$ python3 docs/examples/walkthrough.py clean
$ python3 docs/examples/failure_cases.py no-software-dir
$ python3 docs/examples/shell_demo.py
$ python3 docs/examples/accelerator.py mismatch
$ bash docs/examples/check_install.sh
$ bash docs/examples/check_config.sh
```

`check_install.sh` and `check_config.sh` use `sudo -n`. The install script refuses to replace an existing `/usr/local/sbin/eessi-arch-guard`.
