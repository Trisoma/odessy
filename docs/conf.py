# SPDX-FileCopyrightText: Copyright (c) 2026 Odessy contributors
# SPDX-License-Identifier: Apache-2.0

# Configuration file for the Sphinx documentation builder.
# Modeled on the structure of NVIDIA Warp's documentation.

import os
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from docutils import nodes
from sphinx import addnodes

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
sys.path.insert(0, str(ROOT / "src"))

project = "Odessy"
author = "Odessy contributors"

try:
    from odessy import __version__ as version
except ImportError:
    version = os.environ.get("DOC_VERSION", "dev")

release = version

# -- General configuration ---------------------------------------------------

extensions = [
    "myst_parser",  # Parse Markdown (.md and .mdx) sources.
    "sphinxcontrib.mermaid",
    "sphinx.ext.autosectionlabel",  # Auto-anchor section headings for cross-refs.
    "sphinx.ext.githubpages",  # Emit a .nojekyll file for GitHub Pages.
]

myst_enable_extensions = [
    "colon_fence",  # ````````-style fenced blocks.
    "deflist",
    "tasklist",
]
myst_fence_as_directive = ["mermaid"]
mermaid_version = "12.3.0"
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

# -- Options for HTML output -------------------------------------------------

html_theme = "furo"
html_title = f"Odessy {version}"

html_static_path = ["_static"]
html_css_files = ["custom.css"]

html_theme_options = {
    "sidebar_hide_name": False,
}

html_show_sphinx = False

# Set the canonical URL when building for GitHub Pages.
html_baseurl = os.environ.get("ODESSY_DOCS_BASEURL", "")

# -- sphinx.ext.autosectionlabel --------------------------------------------

autosectionlabel_prefix_document = True


def link_to_repository(app, doctree):
    """Send links to source files and directories to GitHub."""
    source = Path(app.env.doc2path(app.env.docname)).parent
    for link in list(doctree.findall(lambda node: isinstance(
        node, (addnodes.pending_xref, addnodes.download_reference)
    ))):
        if link.get("reftype") != "myst":
            continue
        target = urlsplit(link["reftarget"])
        raw_path = link["reftarget"] if link.get("refdomain") == "doc" else unquote(target.path)
        if not raw_path:
            continue
        candidate = (source / raw_path).resolve()
        if link.get("refdomain") == "doc" and not candidate.exists():
            # MyST strips the source suffix from links to Markdown documents.
            candidate = candidate.with_name(candidate.name + ".md")
        if candidate.is_relative_to(DOCS) or not candidate.is_relative_to(ROOT) or not candidate.exists():
            continue
        route = "tree" if candidate.is_dir() else "blob"
        path = quote(candidate.relative_to(ROOT).as_posix())
        fragment = link.get("reftargetid") or target.fragment
        url = f"https://github.com/Trisoma/odessy/{route}/develop/{path}"
        if fragment:
            url += "#" + quote(fragment, safe="%")
        reference = nodes.reference("", "", refuri=url)
        reference.extend(link.children)
        link.replace_self(reference)


def setup(app):
    app.connect("doctree-read", link_to_repository)
