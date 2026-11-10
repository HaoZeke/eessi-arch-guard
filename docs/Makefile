# Build the HTML documentation. Install docs/requirements.txt first.
SPHINXOPTS = -W --keep-going
SOURCEDIR = .
BUILDDIR = _build

.PHONY: html clean

html:
	python3 -m sphinx $(SPHINXOPTS) -b html "$(SOURCEDIR)" "$(BUILDDIR)/html"

clean:
	rm -rf "$(BUILDDIR)"
