"""twigs tasks: TuiDo's desk on the canvas, with its model bundled in twigs.

    twigs tasks [--demo | --db FILE]
"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def default_db():
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'tuido' / 'tuido.sqlite3'


def sections():
    """The bundled task desk, sharing the standalone TuiDo database."""
    from .model.store import Store
    from .canvas import Desk
    return [('TuiDo', Desk(Store(default_db()), default_db()))]


def main(argv):
    from .model.store import Store
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
