"""Sphinx configuration for the eessi-arch-guard documentation."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src")))

import eessi_arch_guard

project = "eessi-arch-guard"
author = "eessi-arch-guard developers"
copyright = "(c) eessi-arch-guard developers"
version = eessi_arch_guard.__version__
release = version

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx_design",
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "substitution",
]
myst_heading_anchors = 3
myst_substitutions = {"version": release}

autosummary_generate = False
autodoc_member_order = "bysource"
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
}
autoclass_content = "class"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "examples", "Thumbs.db", ".DS_Store"]
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_theme = "pydata_sphinx_theme"
html_title = "eessi-arch-guard"
html_short_title = "eessi-arch-guard"
html_theme_options = {
    "github_url": "https://github.com/stackhands/eessi-arch-guard",
    "search_bar_text": "Search the docs ...",
    "show_nav_level": 2,
    "navigation_depth": 3,
    "collapse_navigation": False,
    "show_toc_level": 2,
    "navbar_align": "left",
    "footer_start": ["copyright"],
    "footer_end": ["sphinx-version"],
    "secondary_sidebar_items": ["page-toc"],
    "primary_sidebar_end": [],
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/stackhands/eessi-arch-guard",
            "icon": "fa-brands fa-github",
            "type": "fontawesome",
        },
    ],
}
html_show_sourcelink = True
html_show_copyright = True
