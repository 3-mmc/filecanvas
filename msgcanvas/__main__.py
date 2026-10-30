"""msgcanvas: mail (and, later, chat) on one canvas you move across.

    msgcanvas [ACCOUNT[/FOLDER]]   open your mail there (Esc leaves)
    msgcanvas setup                start accounts.toml, or make the sync/send configs from it
    msgcanvas import-thunderbird   add the accounts Thunderbird has (not its passwords)
    msgcanvas login NAME           store an account's password (pass), or sign in (OAuth)
    msgcanvas sync [NAME]          fetch new mail (mbsync), then index it (notmuch new)
"""
import os, shutil, subprocess, sys

from . import accounts, filecanvas as fc

LOOK = dict(colmax=40, glyphs='plain', ease=1 if os.environ.get('CDECK_CALM') else 0.3)


def need(*tools):
    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        sys.exit(f"msgcanvas: needs {', '.join(missing)} (Debian: apt install isync notmuch msmtp pass)")


def configured():
    if not os.path.exists(accounts.NOTMUCH):
        sys.exit('msgcanvas: no accounts yet: `msgcanvas setup`')
    os.environ['NOTMUCH_CONFIG'] = accounts.NOTMUCH
    return accounts.load()[1]


def login(name):
    found = {a.name: a for a in configured()}
    if name not in found:
        sys.exit(f"msgcanvas: no account {name!r} (accounts: {', '.join(found) or 'none'})")
    a = found[name]
    if a.auth in accounts.OAUTH:
        need('gpg')
        os.makedirs(os.path.dirname(a.token), exist_ok=True)
        run = ['python3', accounts.OAUTH2, a.token, '--authorize', '--provider', a.auth, '--email', a.address]
        if a.auth == 'microsoft':
            print(f'Signing in {a.address}: open the link it shows, enter the code, and sign in.\n'
                  'It asks for a client secret: there isn\'t one, press Enter.\n')
            run += ['--authflow', 'devicecode', '--client-id', accounts.MICROSOFT_CLIENT]
        else:
            # Google's device codes don't cover Gmail: a browser, coming back to localhost here
            print(f'Signing in {a.address}: it asks for your Google OAuth client\'s id and secret\n'
                  '(console.cloud.google.com, a "Desktop app" client), then shows a link to open.\n')
            run += ['--authflow', 'localhostauthcode']
        sys.exit(subprocess.call(run))
    need('pass')
    if not os.path.exists(os.path.expanduser('~/.password-store/.gpg-id')):
        sys.exit('msgcanvas: pass has no key yet: `pass init <your GPG key id>` first')
    what = {'gmail': 'app password (myaccount.google.com/apppasswords)',
            'yahoo': 'app password (login.yahoo.com/account/security: "Generate app password")'}.get(a.type, 'password')
    print(f'The {what} for {a.address}:')
    sys.exit(subprocess.call(['pass', 'insert', 'mail/' + a.name]))


def import_thunderbird():
    from . import thunderbird
    import tomllib
    found = [(p, thunderbird.accounts(p)) for p in thunderbird.profiles()]
    found = [(p, a) for p, a in found if a]
    if not found:
        sys.exit('msgcanvas: no Thunderbird profile with IMAP accounts found')
    path, entries = found[0]
    if not os.path.exists(accounts.ACCOUNTS):
        main(['setup'])
    with open(accounts.ACCOUNTS, 'rb') as f:
        have = tomllib.load(f).get('account', [])
    known = {a.get('address', '').lower() for a in have}
    taken = {a.get('name') for a in have}
    new = [e for e in entries if e['address'].lower() not in known]
    for e in new:
        while e['name'] in taken:
            e['name'] += '-2'
        taken.add(e['name'])
    if not new:
        print(f'msgcanvas: {accounts.ACCOUNTS} already has every account in {path}')
        return
    with open(accounts.ACCOUNTS, 'a') as f:
        f.write(thunderbird.toml(new, path))
    print(f'Added to {accounts.ACCOUNTS}, from {path}:')
    for e in new:
        how = {'google': 'Google OAuth', 'microsoft': 'Microsoft OAuth'}.get(
            e.get('auth') or ('microsoft' if e['type'] == 'outlook' else ''),
            'app password' if e['type'] in ('gmail', 'yahoo') else 'password')
        print(f"  {e['name']:<16} {e['address']:<36} {how} (Thunderbird: {e['_thunderbird']})")
    print('Check it, then `msgcanvas setup`.')


def sync(name=None):
    configured()
    need('mbsync', 'notmuch')
    status = subprocess.call(['mbsync', '-c', accounts.MBSYNCRC, name or '-a'])
    indexed = subprocess.call(['notmuch', 'new'])
    sys.exit(status or indexed)


def browse(start):
    from . import mail
    found = configured()
    if not found:
        sys.exit(f'msgcanvas: no accounts in {accounts.ACCOUNTS}')
    fc.pick(start, provider=mail.Mail(found), **LOOK)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ''
    if cmd in ('-h', '--help', 'help'):
        print(__doc__.strip())
    elif cmd == 'setup':
        git = lambda key: subprocess.run(['git', 'config', '--global', key], capture_output=True,
                                         text=True).stdout.strip()
        made = accounts.setup(real_name=git('user.name'), email=git('user.email'))   # the template's guesses
        if made:
            print(f"msgcanvas: configs made for {', '.join(a.name for a in made)} in {accounts.CONFIG}\n"
                  'Next: `msgcanvas login NAME` for each, then `msgcanvas sync`.')
    elif cmd == 'import-thunderbird' and len(argv) == 1:
        import_thunderbird()
    elif cmd == 'login' and len(argv) == 2:
        login(argv[1])
    elif cmd == 'sync' and len(argv) <= 2:
        sync(argv[1] if len(argv) == 2 else None)
    elif len(argv) <= 1 and cmd not in ('login', 'sync', 'import-thunderbird'):
        browse(cmd)
    else:
        sys.exit(__doc__.strip())


if __name__ == '__main__':
    main()
