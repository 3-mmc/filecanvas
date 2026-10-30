"""Every test runs against a private, temporary home for calcanvas: set here, before any test
module imports calcanvas, whose paths (cal.DATA, cal.STATE, feeds.CONFIG) are fixed when it's
first imported. If they aren't inside it, nothing runs: tests write to these folders."""
import atexit, os, shutil, tempfile

TMP = tempfile.mkdtemp(prefix='calcanvas-test-')
atexit.register(shutil.rmtree, TMP, True)
for var, sub in (('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'), ('XDG_STATE_HOME', 'state')):
    os.environ[var] = os.path.join(TMP, sub)

from calcanvas import cal, feeds   # noqa: E402

for path in (cal.DATA, cal.STATE, feeds.CONFIG):
    if not os.path.realpath(path).startswith(os.path.realpath(TMP) + os.sep):
        raise SystemExit(f'tests: {path} is outside the test home {TMP}; refusing to run')
