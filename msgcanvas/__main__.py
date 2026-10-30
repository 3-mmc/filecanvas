"""msgcanvas: mail (and, later, chat) on one canvas you move across.

    msgcanvas [ACCOUNT[/FOLDER]]   open your mail there (Esc leaves)
    msgcanvas setup                start accounts.toml, or make the sync/send configs from it
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
    if a.auth == 'microsoft':
        need('gpg')
        os.makedirs(os.path.dirname(a.token), exist_ok=True)
        print(f'Signing in {a.address}: open the link it shows, enter the code, and sign in.\n'
              'It asks for a client secret: there isn\'t one, press Enter.\n')
        sys.exit(subprocess.call(['python3', accounts.OAUTH2, a.token, '--authorize', '--provider', 'microsoft',
                                  '--authflow', 'devicecode', '--email', a.address,
                                  '--client-id', accounts.MICROSOFT_CLIENT]))
    need('pass')
    if not os.path.exists(os.path.expanduser('~/.password-store/.gpg-id')):
        sys.exit('msgcanvas: pass has no key yet: `pass init <your GPG key id>` first')
    what = 'app password (myaccount.google.com/apppasswords)' if a.type == 'gmail' else 'password'
    print(f'The {what} for {a.address}:')
    sys.exit(subprocess.call(['pass', 'insert', 'mail/' + a.name]))


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
    elif cmd == 'login' and len(argv) == 2:
        login(argv[1])
    elif cmd == 'sync' and len(argv) <= 2:
        sync(argv[1] if len(argv) == 2 else None)
    elif len(argv) <= 1 and cmd not in ('login', 'sync'):
        browse(cmd)
    else:
        sys.exit(__doc__.strip())


if __name__ == '__main__':
    main()
