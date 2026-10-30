"""twigs: small trees you move across, on one canvas.

    twigs                        every installed twig that adds to it, side by side
    twigs APP [...]              one of them: files, msg, cal, tasks (`twigs APP help`)
    twigs doctor [FEATURE]       check dependencies; --platform macos|arch|ubuntu|debian, --json
    twigs --version              show the package version
    twigs apps                   which twigs there are, which are installed, and what each needs
    twigs install [APP ...]      select enabled twigs (all if none named); --only APP … also works
                                 source checkouts also make PATH symlinks
"""
import os, sys

from twigs.kit import apps

BIN = os.path.expanduser('~/.local/bin')
LAUNCHER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'twigs-launcher')
LOOK = dict(colmax=40, glyphs='plain', ease=1 if os.environ.get('CDECK_CALM') else 0.3, peek=True)


def listing():
    have = set(apps.installed())
    for name, app in apps.every().items():
        lack = apps.missing(app)
        state = 'installed' if name in have else 'not installed'
        print(f"  {name:<6} {app['summary']:<38} {state}" + (f"  (aka {app['alias']})" if app.get('alias') else ''))
        for what, how in lack:
            print(f'           needs {what}: {how}')
        for feature, needs in app.get('feature_needs', {}).items():
            for what, how in apps.missing(dict(needs=needs)):
                print(f'           {feature} needs {what}: {how}')


def link(name, target=LAUNCHER):
    path = os.path.join(BIN, name)
    os.makedirs(BIN, exist_ok=True)
    if os.path.islink(path) or os.path.exists(path):
        if os.path.realpath(path) == os.path.realpath(target):
            return False
        if not os.path.islink(path):
            print(f'  {path} is a file of its own: left alone')
            return False
        os.remove(path)
    os.symlink(target, path)
    return True


def install(names):
    every = apps.every()
    names = [n for n in names if n != '--only'] or list(every)
    unknown = [n for n in names if n not in every]
    if unknown:
        sys.exit(f"twigs: no twig {', '.join(unknown)} (twigs: {', '.join(every)})")
    apps.keep(names)
    made = ([n for n in ['twigs'] + [every[n]['alias'] for n in names if every[n].get('alias')] if link(n)]
            if os.path.isfile(LAUNCHER) else [])
    print(f"twigs: {', '.join(names)} installed" + (f"; on your PATH now: {', '.join(made)}" if made else ''))
    for n in names:
        for what, how in apps.missing(every[n]):
            print(f'  {n} still needs {what}: {how}')
    if 'files' in names:
        print('  for cdi in your shell: eval "$(twigs files --init bash)"   (zsh, fish likewise)')


def hub(start=''):
    from twigs.kit import hub as h
    found = []
    every = apps.every()
    for name in apps.installed():
        target = every[name].get('hub')
        if target:
            try:
                found += apps.load(target)()
            except (SystemExit, ImportError):   # one not set up yet says so when it's opened on its own
                continue
    if not found:
        sys.exit('twigs: nothing to show yet: `twigs apps` says what each twig needs')
    h.open(found, start, **LOOK)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    alias = {a['alias']: n for n, a in apps.every().items() if a.get('alias')}
    called = os.path.basename(sys.argv[0])
    if called in alias:                 # run as msgcanvas, calcanvas: that twig
        argv = [alias[called]] + argv
    cmd = argv[0] if argv else ''
    every = apps.every()
    if cmd in ('-h', '--help', 'help'):
        print(__doc__.strip())
    elif cmd in ('--version', 'version'):
        from twigs import __version__
        print(f'twigs {__version__}')
    elif cmd == 'doctor':
        from twigs.kit.doctor import main as doctor
        return doctor(argv[1:])
    elif cmd == 'apps':
        listing()
    elif cmd == 'install':
        install(argv[1:])
    elif cmd in every:
        apps.load(every[cmd]['cli'])(argv[1:])
    elif len(argv) <= 1:
        hub(cmd)
    else:
        sys.exit(__doc__.strip())


if __name__ == '__main__':
    sys.exit(main())
