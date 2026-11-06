# SPDX-FileCopyrightText: Copyright (c) 2026 Odessy contributors
# SPDX-License-Identifier: Apache-2.0

# Configuration file for the Sphinx documentation builder.
# Modeled on the structure of NVIDIA Warp's documentation.

import os
import sys

# -- Path setup --------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.realpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

# -- Project information -----------------------------------------------------

project = "Odessy"
author = "Odessy contributors"

# Version is read from pyproject.toml metadata; fall back to "dev" when the
# package is not importable (docs should build without the full runtime).
try:
    from odessy import __version__ as _odessy_version
except Exception:
    _odessy_version = os.environ.get("DOC_VERSION", "dev")

version = _odessy_version
release = _odessy_version

# -- General configuration ---------------------------------------------------

extensions = [
    "myst_parser",  # Parse Markdown (.md and .mdx) sources.
    "sphinx.ext.autosectionlabel",  # Auto-anchor section headings for cross-refs.
    "sphinx.ext.githubpages",  # Emit a .nojekyll file for GitHub Pages.
]

myst_enable_extensions = [
    "colon_fence",  # ````````-style fenced blocks.
    "deflist",
    "tasklist",
]
myst_heading_anchors = 3
# Frontmatter `title:` keys are promoted to the document H1 heading. Several
# existing .mdx files carry their title only in frontmatter, not as a leading
# `#` heading; this keeps those pages correctly titled in the toctree and
# silences the "headings start at H2" warnings.
myst_title_to_header = True

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
    ".mdx": "markdown",
}

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

master_doc = "index"

# Some existing source files contain relative Markdown links to files that live
# outside `docs/` (e.g. top-level README, evals results, THIRD-PARTY licenses).
# Myst tries to resolve those as in-tree cross-references and warns when the
# target has no corresponding document. The links still render as plain URLs,
# so suppress the noise; a follow-up pass should fix the links themselves.
#
# `misc.highlighting_failure` covers code blocks that name a Pygments lexer
# Sphinx does not know (e.g. `mermaid` in ARCHITECTURE.md). The block is still
# rendered as plain text; suppressing keeps the docs build warning-clean until
# a Mermaid renderer is added separately.
suppress_warnings = ["myst.xref_missing", "misc.highlighting_failure"]

# -- Options for HTML output -------------------------------------------------

html_theme = "furo"
html_title = f"Odessy {_odessy_version}"

html_static_path = ["_static"]
html_css_files = ["custom.css"]

html_theme_options = {
    "sidebar_hide_name": False,
}

html_show_sphinx = False

# -- GitHub Pages -----------------------------------------------------------

# GitHub Pages serves project sites under `https://<user>.github.io/<repo>/`.
# Without a base URL, Sphinx emits root-relative asset links (e.g. /_static/...)
# that resolve to the wrong host on GitHub Pages and 404. Point at the published
# site so assets, search, and cross-page links carry the `/odessy/` prefix.
# Local previews leave this unset (empty base URL) so `127.0.0.1` keeps working.
html_baseurl = os.environ.get("ODESSY_DOCS_BASEURL", "")

# -- sphinx.ext.autosectionlabel --------------------------------------------

autosectionlabel_prefix_document = True
