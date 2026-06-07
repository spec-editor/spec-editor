#!/bin/bash
# Record demo GIF — fully automated.
# Usage: bash docs/record-demo.sh

cd "$(dirname "$0")/.."

asciinema rec --overwrite docs/demo.cast \
  -c "PS1='$ ' zsh -fc '.venv/bin/python docs/demo.py'" \
  && agg docs/demo.cast docs/demo.gif \
  && echo "✅ docs/demo.gif" \
  && open docs/demo.gif
