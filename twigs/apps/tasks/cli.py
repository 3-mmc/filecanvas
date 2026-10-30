"""twigs tasks: tuido's desk on the canvas. It needs tuido (github.com/3-mmc/TuiDo): where tuido
is installed in a Python of its own (uv, a venv), its store is borrowed from there.

    twigs tasks [--demo | --db FILE]
"""
import os, shutil, subprocess, sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = str(Path(__file__).resolve().parents[3])        # the folder twigs/ is in


def tuido_python():
    """The Python that has tuido: this one, or the one its `tuido` command runs under."""
    try:
        import tuido  # noqa: F401
        return sys.executable
    except ImportError:
        pass
    found = shutil.which('tuido')
    if found:
        with open(found, 'rb') as f:
            first = f.readline().decode(errors='replace')
        if first.startswith('#!') and os.path.exists(first[2:].strip().split()[0]):
            return first[2:].strip().split()[0]
    return None


def find_tuido():
    """Make tuido importable here, if it's anywhere: already, or where the Python its `tuido`
    command runs under keeps it (its store needs only the standard library, so this Python can
    use it beside mail). Whether it is now."""
    try:
        import tuido.store  # noqa: F401
        return True
    except ImportError:
        pass
    py = tuido_python()
    if not py or py == sys.executable:
        return False
    try:
        where = subprocess.run([py, '-c', 'import os, tuido; print(os.path.dirname(os.path.dirname('
                                'os.path.abspath(tuido.__file__))))'], capture_output=True, text=True,
                               timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    if not where:
        return False
    sys.path.append(where)      # after this Python's own: only tuido is wanted from there
    try:
        import tuido.store  # noqa: F401
        return True
    except ImportError:
        sys.path.remove(where)
        return False


def default_db():
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'tuido' / 'tuido.sqlite3'


def sections():
    """TuiDo, your desk (tuido's own database), if tuido's here: what tasks adds to a canvas."""
    if not find_tuido() or not default_db().exists():
        return []
    from tuido.store import Store
    from .canvas import Desk
    return [('TuiDo', Desk(Store(default_db()), default_db()))]


def main(argv):
    if not find_tuido():
        sys.exit('twigs tasks: needs tuido (github.com/3-mmc/TuiDo): install it, then `twigs install tasks`')
    from tuido.store import Store
    from .canvas import run
    demo = '--demo' in argv
    db = argv[argv.index('--db') + 1] if '--db' in argv[:-1] else None
    with TemporaryDirectory(prefix='tuido-demo-') as tmp:
        path = Path(tmp) / 'demo.sqlite3' if demo else Path(db) if db else default_db()
        store = Store(path)
        try:
            if demo:
                store.seed_demo()
            run(store, None if demo else path)
        finally:
            store.close()
