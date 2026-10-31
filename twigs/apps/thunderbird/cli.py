"""Use Thunderbird's existing accounts without an agent or a separate provider login.

    twigs thunderbird accounts [--node PATH] [--connection-file PATH]
    twigs thunderbird connect [--account ID ...] [--prefer-thunderbird | --include-existing]
    twigs thunderbird sync
    twigs thunderbird status | start | stop | gui | autostart [on|off]
    twigs thunderbird

Requires running Thunderbird with the Thunderbird MCP extension enabled. In a terminal,
connect without --account walks you through it: the extension, which accounts, and keeping
Thunderbird running in the background (start: headless; gui: its window, then headless again).
Account overlaps are skipped by default. --prefer-thunderbird selects Thunderbird as the
mail source; --include-existing shows both sources. Existing mail config is preserved.
"""
import argparse
import sys
import time
from . import host, settings
from .connection import Client
from twigs.kit.text import plain


def sections():
    from .canvas import Thunderbird, NotConnected
    config = settings.load()
    if config and config['accounts']:
        return [('Emails', Thunderbird(config))]
    # The extension's there but nothing's connected yet: say how, rather than show nothing.
    profile = host.chosen() if config is None else None
    return [('Emails', NotConnected())] if profile and profile.extension == 'enabled' else []


STEPS = '''In Thunderbird: Tools → Add-ons and Themes → the gear menu → Install Add-on From File…
  and choose {path}
  Allow what it asks. In its Options, check the server is running and which accounts it may read.'''


def yes(answer, default=False):
    answer = answer.strip().lower()
    return default if not answer else answer in ('y', 'yes', 'j', 'ja')


def guide(config, ask=input, say=print, wait=600):
    """Setting up from nothing, a step at a time: the profile, the extension (downloaded if you
    agree), Thunderbird running. Returns Thunderbird's accounts once it answers."""
    profile = host.chosen()
    if profile is None:
        raise OSError('No Thunderbird profile found. Install Thunderbird, add your accounts there, then run this again.')
    say(f'Thunderbird profile: {profile.name}')
    if profile.extension == 'disabled':
        say('Thunderbird MCP is installed but disabled: enable it in Tools → Add-ons and Themes.')
    elif profile.extension is None:
        if not yes(ask(f'Download the Thunderbird MCP extension {host.EXTENSION_VERSION} from GitHub? [y/N] ')):
            raise OSError('Not downloaded. To install it yourself, see docs/thunderbird.md.')
        xpi = host.fetch_extension()
        say(f'Downloaded and checked: {xpi}')
    if profile.extension != 'enabled':
        now = host.state()[0]
        if now != 'window':
            say('Opening Thunderbird…')
            if now == 'headless':
                host.window(profile, then_headless=False)
            else:
                host.start(profile, headless=False)
        if profile.extension is None:
            say(STEPS.format(path=host.shown_path(xpi)))
    elif host.state()[0] == 'stopped':
        if not yes(ask("Thunderbird isn't running. Start it in the background now? [Y/n] "), True):
            raise OSError('Start Thunderbird, then run this again.')
        host.start(profile)
    say('Waiting for Thunderbird to answer… (Ctrl+C stops)')
    began, nudged = time.monotonic(), False
    try:
        while True:
            try:
                with Client(config, timeout=10) as client:
                    return client.call('listAccounts')
            except OSError:
                if time.monotonic() > began + wait:
                    raise OSError("Thunderbird didn't answer. Check the extension's Options, then run this again.") from None
                if not nudged and host.extension(profile.path) == 'enabled' and time.monotonic() > began + 30:
                    say('The extension is installed but not answering yet: restart Thunderbird if it asked you to.')
                    nudged = True
                time.sleep(2)
    except KeyboardInterrupt:
        raise OSError('Stopped waiting. Run twigs thunderbird connect again when Thunderbird is ready.') from None


def pick(accounts, chosen, ask=input, say=print):
    """Which of `chosen` (Thunderbird's account IDs) to connect: all, unless you name some."""
    names = {a['id']: a for a in accounts}
    for ident in chosen:
        say(f"  {plain(ident)}: {plain(names[ident]['name'])}")
    answer = ask('Connect all of these? [Y/n, or the IDs you want] ').strip()
    if answer.lower() in ('', 'y', 'yes', 'j', 'ja'):
        return chosen
    if answer.lower() in ('n', 'no', 'nein'):
        return []
    wanted = answer.replace(',', ' ').split()
    unknown = set(wanted) - names.keys()
    if unknown:
        raise ValueError('Unknown Thunderbird account IDs: ' + ', '.join(sorted(unknown)))
    return wanted


def keep_running(ask=input, say=print):
    """Offer to start Thunderbird headless at login (and now, if it isn't running)."""
    profile = host.chosen()
    if profile is None or host.autostarts():
        return
    if not yes(ask('Keep Thunderbird running in the background for twigs, from each login? [Y/n] '), True):
        say('Then keep Thunderbird open (minimised is fine) while you use twigs, or: twigs thunderbird start')
        return
    say(f'Starts at login: {host.autostart(profile)}')
    now = host.state()[0]
    if now == 'stopped':
        host.start(profile)
        say('Thunderbird is running in the background now.')
    elif now == 'window':
        say('You can close the Thunderbird window: from the next login it runs in the background. '
            'twigs thunderbird start runs it in the background now.')


def status(say=print):
    profile = host.chosen()
    if profile is None:
        say('Thunderbird: no profile found')
        return 1
    now = host.state()[0]
    config = settings.load()
    say(f'Thunderbird profile: {profile.name} ({profile.kind})')
    say('Thunderbird MCP: ' + (profile.extension or 'not installed'))
    say('Thunderbird: ' + dict(headless='running in the background', window='open in a window', stopped='not running')[now])
    say('Starts at login: ' + ('yes' if host.autostarts() else 'no'))
    say('Connected accounts: ' + (str(len(config['accounts'])) if config else 'none yet (twigs thunderbird connect)'))
    return 0 if profile.extension == 'enabled' and now != 'stopped' and config else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command')
    for name in ('accounts', 'connect'):
        s = sub.add_parser(name)
        s.add_argument('--node', help='Node executable; use Windows node.exe for Windows Thunderbird from WSL')
        s.add_argument('--connection-file', help='Override upstream connection discovery (not a copied token)')
        if name == 'connect':
            s.add_argument('--account', action='append', help='Thunderbird account ID; repeat to select several')
            source = s.add_mutually_exclusive_group()
            source.add_argument('--prefer-thunderbird', action='store_true',
                                help='Use Thunderbird for selected accounts; hide overlapping manual mail views')
            source.add_argument('--include-existing', action='store_true', help='Show both sources for overlapping accounts')
    sub.add_parser('sync', help='Check the connection and list selected account folder counts')
    sub.add_parser('status', help='The profile, extension, whether Thunderbird runs, and what is connected')
    sub.add_parser('start', help='Start Thunderbird in the background (headless)')
    sub.add_parser('stop', help='Stop Thunderbird running in the background')
    sub.add_parser('gui', help="Open Thunderbird's window; back in the background when it closes")
    s = sub.add_parser('autostart', help='Start Thunderbird in the background at login, or not')
    s.add_argument('switch', nargs='?', choices=('on', 'off'), default='on')
    args = p.parse_args(argv)
    try:
        if args.command == 'status':
            return status()
        if args.command in ('start', 'gui', 'autostart'):
            profile = host.chosen()
            if profile is None:
                p.error('No Thunderbird profile found; set up Thunderbird first')
            if args.command == 'start':
                print(dict(headless='Thunderbird is running in the background.',
                           window='Thunderbird is already open in a window.')[host.start(profile)])
            elif args.command == 'gui':
                host.window(profile)
            else:
                path = host.autostart(profile, args.switch == 'on')
                print(f'Starts at login: {path}' if args.switch == 'on' else 'No longer starts at login.')
            return
        if args.command == 'stop':
            host.stop()
            print('Thunderbird stopped.')
            return
        if args.command in ('accounts', 'connect'):
            config = dict(settings.load() or {})
            for k in ('node', 'connection_file'):
                if getattr(args, k) is not None:
                    config[k] = getattr(args, k)
            guided = args.command == 'connect' and not args.account and sys.stdin.isatty() and sys.stdout.isatty()
            try:
                with Client(config) as client:
                    accounts = client.call('listAccounts')
            except OSError as e:
                if not guided:
                    raise OSError(host.hint() or str(e)) from None
                accounts = guide(config)
            addresses = settings.existing_addresses()
            if args.command == 'accounts':
                for a in accounts:
                    emails = ', '.join(plain(i.get('email')) for i in a.get('identities', []))
                    if a['id'] in config.get('preferred_accounts', {}) and a['id'] in config.get('accounts', []):
                        overlap = ' · using Thunderbird'
                    else:
                        overlap = ' · already in Twigs (choose --prefer-thunderbird to use Thunderbird)' if settings.overlapping(a, addresses) else ''
                    print(f"{plain(a['id'])}: {plain(a['name'])} [{emails or a.get('type', '')}]{overlap}")
                if not accounts:
                    print('No accessible Thunderbird accounts; check the extension account permissions.')
                return
            selected, skipped = settings.choose(accounts, args.account, args.include_existing or args.prefer_thunderbird, addresses)
            for a in skipped:
                print(f"Skipped {plain(a['name'])}: already configured in Twigs.")
            if guided and selected:
                selected = pick(accounts, selected)
            if not selected:
                p.error('No new accounts selected. Use --account ID --prefer-thunderbird to use Thunderbird, or --include-existing to show both sources.')
            config.update(accounts=selected, include_existing=args.include_existing)
            config.pop('preferred_accounts', None)
            if args.prefer_thunderbird:
                config['preferred_accounts'] = {
                    a['id']: [i['email'].strip().casefold() for i in a.get('identities', []) if i.get('email')]
                    for a in accounts if a['id'] in selected}
            settings.save(config)
            from twigs.kit import apps
            apps.keep(apps.installed()+['thunderbird'])
            print(f'Connected {len(selected)} Thunderbird account(s). Open with twigs thunderbird.')
            if guided:
                keep_running()
        elif args.command == 'sync':
            config = settings.load()
            if not config:
                p.error('Connect first: twigs thunderbird connect')
            with Client(config) as client:
                accounts = client.call('listAccounts')
                selected, skipped = settings.selection(accounts, config)
                for a in accounts:
                    if a['id'] in selected and a['id'] in config['accounts']:
                        folders = client.call('listFolders', dict(accountId=a['id'], format='objects'))
                        print(f"{plain(a['name'])}: {len(folders)} folders (Thunderbird connection verified)")
                for a in skipped:
                    print(f"Skipped {plain(a['name'])}: now configured in another Twigs mail source.")
        else:
            from twigs.kit import hub
            got = sections()
            if not got:
                p.error('Connect first: twigs thunderbird connect')
            hub.open(got, intro=False, colmax=66, glyphs='plain', peek=True)
    except (OSError, ValueError, KeyError, TypeError) as e:
        p.exit(1, f'thunderbird: {plain(e)}\n')
