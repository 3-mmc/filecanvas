"""Every test runs against a private, temporary home for msgcanvas: set here, before any test
module imports msgcanvas, whose paths (accounts.CONFIG, DATA, compose.DRAFTS) are fixed when it's
first imported. If they aren't inside it, nothing runs: tests delete and rewrite these folders."""
import atexit, os, shutil, tempfile

TMP = tempfile.mkdtemp(prefix='msgcanvas-test-')
atexit.register(shutil.rmtree, TMP, True)
for var, sub in (('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'), ('XDG_STATE_HOME', 'state'),
                 ('XDG_CACHE_HOME', 'cache')):
    os.environ[var] = os.path.join(TMP, sub)
os.environ['NOTMUCH_CONFIG'] = os.path.join(TMP, 'notmuch-config')

from msgcanvas import accounts, compose, files   # noqa: E402

for path in (accounts.CONFIG, accounts.DATA, compose.DRAFTS, files.CACHE):
    if not os.path.realpath(path).startswith(os.path.realpath(TMP) + os.sep):
        raise SystemExit(f'tests: {path} is outside the test home {TMP}; refusing to run')
