import sys

from src.main import cli

# When run as `python -m src.main`, sys.argv[0] is __main__.py path.
# Click uses it as prog_name — normalise to "spec-editor".
if not sys.argv[0].endswith("spec-editor"):
    sys.argv[0] = "spec-editor"

cli()
