project = "Model Fleet Operator"
author = "Model Fleet contributors"
release = "0.1.0"

extensions = ["myst_parser", "sphinxcontrib.mermaid"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
exclude_patterns = ["_build"]
html_theme = "sphinx_rtd_theme"
html_title = "Model Fleet Operator"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
myst_enable_extensions = ["colon_fence", "deflist"]
mermaid_version = "11.6.0"
