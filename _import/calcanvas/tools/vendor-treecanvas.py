#!/usr/bin/env python3
"""Copy treecanvas.py from a treecanvas checkout into calcanvas/treecanvas.py, unchanged except
for a header saying where it came from. Edit treecanvas itself and run this again; never edit the
copy. calcanvas's own choices live in its providers and __main__.py.

    tools/vendor-treecanvas.py [path/to/treecanvas]     (default: ~/lab/treecanvas)
"""
import os, subprocess, sys

source = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else '~/lab/treecanvas')
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
target = os.path.join(here, 'calcanvas', 'treecanvas.py')
commit = subprocess.run(['git', '-C', source, 'rev-parse', '--short', 'HEAD'],
                        capture_output=True, text=True).stdout.strip() or 'unknown'
dirty = subprocess.run(['git', '-C', source, 'status', '--porcelain', 'treecanvas.py'],
                       capture_output=True, text=True).stdout.strip()
if dirty:
    sys.exit(f'{source}/treecanvas.py has uncommitted changes; commit them first')
with open(os.path.join(source, 'treecanvas.py')) as f:
    text = f.read()
if text.startswith('#!'):
    text = text.split('\n', 1)[1]
with open(target, 'w') as f:
    f.write(f'# Vendored from treecanvas (github.com/3-mmc/treecanvas) at {commit}: do not edit here.\n'
            '# Change it there and run tools/vendor-treecanvas.py; calcanvas\'s own choices are in its providers.\n'
            + text)
print(f'calcanvas/treecanvas.py <- treecanvas {commit}')
