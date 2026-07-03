"""Fix multi-ID extraction in _scan_implements."""
from pathlib import Path
import re

p = Path('/Users/dmitry/Documents/Droid/spec-editor2/src/cli/commands_coverage.py')
c = p.read_text()

# Fix 1: Multi-ID regex
old1 = """for m in re.finditer(r'^@implements\\([\"']([^\"']+)[\"']', content, flags=re.MULTILINE):
            spec_id = m.group(1)"""
new1 = """for m in re.finditer(r'^@implements\\((.+?)\\)', content, flags=re.MULTILINE):
            args_str = m.group(1)
            ids = re.findall(r'''['"]([^'\"]+)['\"]''', args_str)
            for spec_id in ids:"""
c = c.replace(old1, new1)

# Fix 2: Body indentation
old2 = """            spec_id = m.group(1)"""
# Already replaced above
old3 = """            line_no = content[:m.start()].count(\"\\n\") + 1
            rel_path = str(py_file.relative_to(proj))
            implements_map.setdefault(spec_id, []).append(f\"{rel_path}:{line_no}\")"""
new3 = """            for spec_id in ids:
                line_no = content[:m.start()].count(\"\\n\") + 1
                rel_path = str(py_file.relative_to(proj))
                implements_map.setdefault(spec_id, []).append(f\"{rel_path}:{line_no}\")"""
c = c.replace(old3, new3)

p.write_text(c)
print('Done - multi-ID extraction fixed')
