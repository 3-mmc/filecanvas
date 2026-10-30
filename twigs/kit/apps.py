"""The twigs there are: the ones in twigs/apps, each a package with an APP manifest, and any other
package's, registered under the entry point group 'twigs.apps' (its value a module with an APP).

    APP = dict(name='mail', title='mail & chats', alias='msgcanvas', summary='…',
               cli='pkg.module:main',          # `twigs NAME …` runs main(argv)
               hub='pkg.module:sections',      # what it adds to `twigs` alone: [(name, Provider)], or None
               needs=[('mbsync', 'isync')])    # (a command, 'module:x' or 'command:x', how to get it)

Which are installed is kept in ~/.config/twigs/installed (`twigs install`)."""
import importlib, importlib.util, json, os, shutil

BUILT_IN = ('files', 'msg', 'cal', 'tasks')
CONFIG = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'twigs')
INSTALLED = os.path.join(CONFIG, 'installed')


def load(target):
    """'pkg.module:attr' -> the attribute."""
    module, _, attr = target.partition(':')
    return getattr(importlib.import_module(module), attr)


def every():
    """{name: APP}, the built-in ones first, then the ones other packages register."""
    found = {}
    for name in BUILT_IN:
        app = importlib.import_module(f'twigs.apps.{name}').APP
        found[app['name']] = app
    try:
        from importlib.metadata import entry_points
        for ep in entry_points(group='twigs.apps'):
            try:
                app = ep.load().APP
                found.setdefault(app['name'], app)
            except Exception:       # one that won't load doesn't take the rest with it
                continue
    except Exception:
        pass
    return found


def missing(app):
    """What `app` needs that isn't here: [(what, how to get it)]."""
    out = []
    for what, how in app.get('needs', ()):
        kind, _, name = what.partition(':') if ':' in what else ('command', '', what)
        if kind == 'module':
            ok = importlib.util.find_spec(name) is not None
        else:
            ok = shutil.which(name) is not None
        if not ok:
            out.append((name, how))
    return out


def installed():
    try:
        with open(INSTALLED) as f:
            chosen = set(json.load(f))
        return [n for n in every() if n in chosen]          # in the twigs' own order, not as kept
    except (OSError, ValueError):
        return list(BUILT_IN)       # none chosen yet: all of them


def keep(names):
    os.makedirs(CONFIG, exist_ok=True)
    with open(INSTALLED, 'w') as f:
        json.dump(sorted(set(names)), f)
