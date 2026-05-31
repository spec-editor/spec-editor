#!/bin/bash
# Generate a terminal demo of spec-editor in action.
# Requires: asciinema (brew install asciinema)
#
# Usage: bash docs/demo.sh
# Output: demo.cast (play with: asciinema play demo.cast)

RECORDING="demo.cast"

# Start recording
asciinema rec --overwrite "$RECORDING" -c "
# Spec Editor Demo
echo '=== Spec Editor Demo ==='
echo ''

# 1. Create project
echo '$ spec-editor init demo-project --with-example'
spec-editor init /tmp/spec-demo-gif --with-example 2>&1 | tail -5
echo ''

# 2. Show source
echo '$ cat demo-project/source/readme.md | head -10'
head -10 /tmp/spec-demo-gif/source/readme.md
echo '...'
echo ''

# 3. Demo mode
echo '$ spec-editor demo'
spec-editor demo 2>&1 | head -15
echo ''

# 4. View graph
echo 'Opening spec graph in browser...'
echo ''

# 5. Validate
echo '$ spec-editor validate -p /tmp/spec-demo-gif'
spec-editor validate -p /tmp/spec-demo-gif 2>&1 | grep -v debug
echo ''

echo 'Done! Try spec-editor init my-project --with-example'
sleep 2
"

echo "Recording saved: $RECORDING"
echo "Play: asciinema play $RECORDING"
echo "Upload: asciinema upload $RECORDING"
