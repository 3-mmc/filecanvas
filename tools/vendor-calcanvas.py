#!/usr/bin/env python3
"""Copy calcanvas's calendar (cal.py, feeds.py) from a calcanvas checkout into msgcanvas/,
unchanged except for a header saying where it came from. Edit calcanvas itself and run this
again; never edit the copies. Both read and write the same calendars and what-ifs.

    tools/vendor-calcanvas.py [path/to/calcanvas]     (default: ~/lab/calcanvas)
"""
import os, subprocess, sys

source = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else '~/lab/calcanvas')
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
commit = subprocess.run(['git', '-C', source, 'rev-parse', '--short', 'HEAD'],
                        capture_output=True, text=True).stdout.strip() or 'unknown'
for name in ('cal.py', 'feeds.py'):
    path = os.path.join(source, 'calcanvas', name)
    if subprocess.run(['git', '-C', source, 'status', '--porcelain', path], capture_output=True,
                      text=True).stdout.strip():
        sys.exit(f'{path} has uncommitted changes; commit them first')
    with open(path) as f:
        text = f.read()
    with open(os.path.join(here, 'msgcanvas', name), 'w') as f:
        f.write(f'# Vendored from calcanvas (github.com/3-mmc/calcanvas) at {commit}: do not edit here.\n'
                '# Change it there and run tools/vendor-calcanvas.py.\n' + text)
print(f'msgcanvas/cal.py, feeds.py <- calcanvas {commit}')
