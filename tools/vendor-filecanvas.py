#!/usr/bin/env python3
"""Copy filecanvas.py from a filecanvas checkout into msgcanvas/filecanvas.py, unchanged except
for a header saying where it came from. Edit filecanvas itself and run this again; never edit the
copy. msgcanvas's own choices live in its providers and __main__.py.

    tools/vendor-filecanvas.py [path/to/filecanvas]     (default: ~/lab/filecanvas-providers, until feature/providers is merged)
"""
import os, subprocess, sys

source = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else '~/lab/filecanvas-providers')
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
target = os.path.join(here, 'msgcanvas', 'filecanvas.py')
commit = subprocess.run(['git', '-C', source, 'rev-parse', '--short', 'HEAD'],
                        capture_output=True, text=True).stdout.strip() or 'unknown'
dirty = subprocess.run(['git', '-C', source, 'status', '--porcelain', 'filecanvas.py'],
                       capture_output=True, text=True).stdout.strip()
if dirty:
    sys.exit(f'{source}/filecanvas.py has uncommitted changes; commit them first')
with open(os.path.join(source, 'filecanvas.py')) as f:
    text = f.read()
if text.startswith('#!'):
    text = text.split('\n', 1)[1]
with open(target, 'w') as f:
    f.write(f'# Vendored from filecanvas (github.com/3-mmc/filecanvas) at {commit}: do not edit here.\n'
            '# Change it there and run tools/vendor-filecanvas.py; msgcanvas\'s own choices are in its providers.\n'
            + text)
print(f'msgcanvas/filecanvas.py <- filecanvas {commit}')
