"""twigs tasks: tuido's desk on the canvas. It needs tuido (github.com/3-mmc/TuiDo): where tuido
is installed in a Python of its own (uv, a venv), this runs in that one, with twigs beside it.

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


def main(argv):
    try:
        import tuido  # noqa: F401
    except ImportError:
        py = tuido_python()
        if not py or py == sys.executable:
            sys.exit('twigs tasks: needs tuido (github.com/3-mmc/TuiDo): install it, then `twigs install tasks`')
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [ROOT, os.environ.get('PYTHONPATH')])))
        sys.exit(subprocess.call([py, '-m', 'twigs', 'tasks', *argv], env=env))
    from tuido.store import Store
    from .canvas import run
    demo = '--demo' in argv
    db = argv[argv.index('--db') + 1] if '--db' in argv[:-1] else None
    default = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'tuido' / 'tuido.sqlite3'
    with TemporaryDirectory(prefix='tuido-demo-') as tmp:
        path = Path(tmp) / 'demo.sqlite3' if demo else Path(db) if db else default
        store = Store(path)
        try:
            if demo:
                store.seed_demo()
            run(store, None if demo else path)
        finally:
            store.close()
