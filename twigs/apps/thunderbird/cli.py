"""Use Thunderbird's existing accounts without an agent or a separate provider login.

    twigs thunderbird accounts [--node PATH] [--connection-file PATH]
    twigs thunderbird connect [--account ID ...] [--include-existing]
    twigs thunderbird sync
    twigs thunderbird

Requires running Thunderbird with the Thunderbird MCP extension enabled.
Account overlaps with existing Twigs mail are excluded unless --include-existing is given.
"""
import argparse
import sys
from . import settings
from .connection import Client
from twigs.kit.text import plain


def sections():
    from .canvas import Thunderbird
    config = settings.load()
    return [('Thunderbird', Thunderbird(config))] if config and config['accounts'] else []


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command')
    for name in ('accounts', 'connect'):
        s = sub.add_parser(name)
        s.add_argument('--node', help='Node executable; use Windows node.exe for Windows Thunderbird from WSL')
        s.add_argument('--connection-file', help='Override upstream connection discovery (not a copied token)')
        if name == 'connect':
            s.add_argument('--account', action='append', help='Thunderbird account ID; repeat to select several')
            s.add_argument('--include-existing', action='store_true', help='Also include accounts already configured in Twigs')
    sub.add_parser('sync', help='Check the connection and list selected account folder counts')
    args = p.parse_args(argv)
    try:
        if args.command in ('accounts', 'connect'):
            config = dict(settings.load() or {})
            for k in ('node', 'connection_file'):
                if getattr(args, k) is not None:
                    config[k] = getattr(args, k)
            with Client(config) as client:
                accounts = client.call('listAccounts')
            addresses = settings.existing_addresses()
            if args.command == 'accounts':
                for a in accounts:
                    emails = ', '.join(plain(i.get('email')) for i in a.get('identities', []))
                    overlap = ' · already in Twigs (skipped by default)' if settings.overlapping(a, addresses) else ''
                    print(f"{plain(a['id'])}: {plain(a['name'])} [{emails or a.get('type', '')}]{overlap}")
                if not accounts:
                    print('No accessible Thunderbird accounts; check the extension account permissions.')
                return
            selected, skipped = settings.choose(accounts, args.account, args.include_existing, addresses)
            for a in skipped:
                print(f"Skipped {plain(a['name'])}: already configured in Twigs.")
            if not selected:
                p.error('No new accounts selected. To include an overlapping account, use --account ID --include-existing.')
            config.update(accounts=selected, include_existing=args.include_existing)
            settings.save(config)
            from twigs.kit import apps
            apps.keep(apps.installed()+['thunderbird'])
            print(f'Connected {len(selected)} Thunderbird account(s). Open with twigs thunderbird.')
        elif args.command == 'sync':
            config = settings.load()
            if not config:
                p.error('Connect first: twigs thunderbird connect')
            with Client(config) as client:
                accounts = client.call('listAccounts')
                selected, skipped = settings.choose(accounts, config['accounts'], config.get('include_existing', False))
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
