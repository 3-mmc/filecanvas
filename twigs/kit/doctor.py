"""Read-only dependency diagnostics. No installs, login attempts or credential inspection."""
import argparse
import importlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys

PLATFORMS = ('macos', 'arch', 'ubuntu', 'debian', 'linux', 'windows')
RECIPES = {
    'ubuntu': {
        'mail': 'sudo apt install isync notmuch python3-notmuch2 msmtp pass gnupg',
        'oauth': 'sudo apt install neomutt libsasl2-modules-kdexoauth2',
        'media': 'sudo apt install chafa',
        'thunderbird': 'sudo apt install thunderbird nodejs; then twigs thunderbird connect.',
    },
    'arch': {
        'mail': 'sudo pacman -S isync notmuch python-cffi msmtp pass gnupg',
        'oauth': 'sudo pacman -S neomutt; ensure your SASL plugins provide XOAUTH2 for mbsync',
        'media': 'sudo pacman -S chafa',
        'thunderbird': 'sudo pacman -S thunderbird nodejs; then twigs thunderbird connect.',
    },
    'macos': {
        'mail': 'brew install isync notmuch msmtp pass gnupg',
        'oauth': 'brew install neomutt; locate mutt_oauth2.py and set TWIGS_OAUTH_HELPER to its path',
        'media': 'brew install chafa',
        'thunderbird': 'brew install --cask thunderbird; brew install node; then twigs thunderbird connect.',
    },
}
RECIPES['debian'] = RECIPES['ubuntu']
FEATURES = ('base', 'files', 'cal', 'tasks', 'mail', 'chat', 'oauth', 'media', 'thunderbird')


def detect(system=None, release=None):
    system = system or sys.platform
    if system == 'darwin':
        return 'macos'
    if system == 'win32':
        return 'windows'
    if release is None:
        try:
            release = platform.freedesktop_os_release()
        except OSError:
            release = {}
    ids = [release.get('ID', ''), *release.get('ID_LIKE', '').split()]
    return next((name for name in ids if name in ('ubuntu', 'debian', 'arch')), 'linux')


def importable(name):
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def recipe(where, feature):
    if feature == 'chat':
        return ('Install the twigs nchat fork with --serve and the protocols you need: '
                'https://github.com/3-mmc/nchat (see docs/nchat-serve.md). '
                'A distribution nchat build may not include --serve or Instagram.')
    if where == 'windows':
        return 'Run twigs inside WSL; native Windows terminal support is not implemented.'
    return RECIPES.get(where, {}).get(feature, 'Install the missing tools with your system package manager.')


def diagnose(features=None, where=None, command=None, module=None, probe=True):
    from twigs.apps.msg import accounts
    from twigs.apps.msg import FEATURE_NEEDS
    where = where or detect()
    command, module = command or shutil.which, module or importable
    selected = list(features or FEATURES)
    checks = []

    def add(feature, name, ok, hint=''):
        checks.append(dict(feature=feature, name=name, ok=bool(ok), hint='' if ok else hint))

    add('base', 'Python 3.11+', sys.version_info >= (3, 11), 'Install Python 3.11 or newer.')
    add('base', 'POSIX terminal', os.name == 'posix', recipe('windows', 'base'))
    if 'tasks' in selected or 'base' in selected:
        try:
            from twigs.apps.tasks.model.store import Store
            store = Store(':memory:')
            try:
                store.db.serialize()
            finally:
                store.close()
            ready = True
        except (AttributeError, sqlite3.Error):
            ready = False
        add('tasks', 'bundled TuiDo model / SQLite snapshots', ready,
            'Use a Python build with SQLite serialize/deserialize support; standalone TuiDo is not needed.')
    for feature in ('files', 'cal'):
        if feature in selected:
            add(feature, 'included; standard library only', True)
    for feature in ('mail', 'chat'):
        if feature not in selected:
            continue
        for requirement, _ in FEATURE_NEEDS[feature]:
            kind, _, name = requirement.partition(':') if ':' in requirement else ('command', '', requirement)
            if feature == 'chat':
                name = accounts.chat_settings()['nchat']
            ok = module(name) if kind == 'module' else command(name)
            hint = recipe(where, feature)
            if kind == 'module':
                hint += ('\nThe binding must import in the Python running twigs. If installed by apt/brew/pacman, '
                         'use that package manager\'s Python and a venv created with --system-site-packages. '
                         'An isolated uv tool install cannot automatically see the system binding. '
                         'Do not copy a native binding between Python versions.')
            add(feature, name, ok, hint)
            if feature == 'chat' and ok and probe:
                try:
                    got = subprocess.run([name, '--help'], capture_output=True, text=True, timeout=5)
                    serve = '--serve' in got.stdout + got.stderr
                except (OSError, subprocess.SubprocessError):
                    serve = False
                add(feature, 'nchat --serve capability', serve, recipe(where, 'chat'))
    if 'oauth' in selected:
        add('oauth', 'gpg', command('gpg'), recipe(where, 'mail'))
        helper = accounts.oauth_helper()
        add('oauth', 'mutt_oauth2.py helper', os.path.isfile(helper), recipe(where, 'oauth') +
            '\nAlternatively set TWIGS_OAUTH_HELPER to your downloaded NeoMutt helper. Never paste tokens into doctor.')
    if 'media' in selected:
        add('media', 'chafa (optional image previews)', command('chafa'), recipe(where, 'media'))
    if 'thunderbird' in selected:
        from twigs.apps.thunderbird import settings
        from twigs.apps.thunderbird.connection import BRIDGE
        try:
            config = settings.load() or {}
        except (OSError, ValueError):
            config = {}
        node = config.get('node') or command('node')
        add('thunderbird', 'bundled MCP bridge', BRIDGE.is_file(), 'Reinstall twigs; its Thunderbird bridge is missing.')
        add('thunderbird', 'node', bool(node and (command(node) or os.path.isfile(node))), recipe(where, 'thunderbird'))
        if node and probe:
            try:
                got = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=5)
                modern = got.returncode == 0 and int(got.stdout.strip().lstrip('v').split('.')[0]) >= 18
            except (OSError, subprocess.SubprocessError, ValueError):
                modern = False
            add('thunderbird', 'Node.js 18+', modern, recipe(where, 'thunderbird'))
        from twigs.apps.thunderbird import host
        profile = host.chosen()
        install = ('Install Thunderbird on Windows; twigs in WSL finds it there.' if host.where() == 'windows'
                   else recipe(where, 'thunderbird'))
        try:
            executable = profile is not None and host.executable(profile)
        except OSError as e:
            executable = False
            install += '\n' + str(e)
        add('thunderbird', 'Thunderbird profile', executable,
            install + '\nThen add your accounts in Thunderbird.')
        if profile is not None:
            add('thunderbird', f'Thunderbird MCP extension in {profile.name}', profile.extension == 'enabled',
                'Enable it in Tools → Add-ons and Themes.' if profile.extension == 'disabled' else
                'twigs thunderbird connect downloads it and walks you through installing it.')
            if probe:
                try:
                    running = host.state(profile=profile)[0] in ('headless', 'window')
                except OSError:
                    running = False
                add('thunderbird', 'Thunderbird running', running,
                    'twigs thunderbird start (in the background), or open Thunderbird; '
                    'twigs thunderbird autostart starts it at each login.')
        add('thunderbird', 'accounts connected to twigs', bool(config.get('accounts')), 'twigs thunderbird connect')
    return dict(schema_version=1, platform=where, python=sys.executable, checks=checks)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('feature', nargs='?', choices=FEATURES + ('msg',))
    parser.add_argument('--platform', choices=PLATFORMS, help='override installation guidance, not the detected runtime')
    parser.add_argument('--json', action='store_true', help='machine-readable diagnostics')
    args = parser.parse_args(argv)
    selected = ['mail', 'chat'] if args.feature == 'msg' else [args.feature] if args.feature else None
    report = diagnose(selected, args.platform)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f'twigs doctor · {report["platform"]} · {report["python"]}')
        for c in report['checks']:
            print(f'  {"ok" if c["ok"] else "missing":7} {c["feature"]:6} {c["name"]}')
            if c['hint']:
                print('          ' + c['hint'].replace('\n', '\n          '))
        if any(c['feature'] == 'thunderbird' for c in report['checks']):
            print('Thunderbird needs its MCP extension enabled and to be running. twigs thunderbird connect '
                  'walks you through it; see docs/thunderbird.md for running it in the background.')
        print('Checks are local and read-only. Missing optional features do not prevent the other twigs from running.')
    required = {'base', *(selected or [])}
    return int(any(not c['ok'] for c in report['checks'] if c['feature'] in required))
