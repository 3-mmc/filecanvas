"""Use Thunderbird's existing accounts without an agent or a separate provider login.

    twigs thunderbird accounts [--node PATH] [--connection-file PATH]
    twigs thunderbird connect [--account ID ...] [--prefer-thunderbird | --include-existing]
    twigs thunderbird sync
    twigs thunderbird

Requires running Thunderbird with the Thunderbird MCP extension enabled.
Account overlaps are skipped by default. --prefer-thunderbird selects Thunderbird as the
mail source; --include-existing shows both sources. Existing mail config is preserved.
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
            source = s.add_mutually_exclusive_group()
            source.add_argument('--prefer-thunderbird', action='store_true',
                                help='Use Thunderbird for selected accounts; hide overlapping manual mail views')
            source.add_argument('--include-existing', action='store_true', help='Show both sources for overlapping accounts')
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
