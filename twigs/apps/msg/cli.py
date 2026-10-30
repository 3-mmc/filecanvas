"""msgcanvas: mail, chat and your calendar on one canvas you move across.

    msgcanvas [ACCOUNT[/FOLDER]]   open your mail there (Esc leaves)
    msgcanvas cal [YYYY-MM]        the calendar alone, on this month (or that one)
    msgcanvas setup                start accounts.toml, or make the sync/send configs from it
    msgcanvas import-thunderbird   add the accounts Thunderbird has (not its passwords)
    msgcanvas login NAME [--paste] store an account's password (pass), or sign in (OAuth)
    msgcanvas sync [NAME]          fetch new mail (mbsync), then index it (notmuch new)
    msgcanvas send-due             send the drafts whose time has come (⌃Z send later), for cron
    msgcanvas chat-setup           add a chat account (WhatsApp, Signal, Instagram) with nchat
"""
import os, shutil, subprocess, sys

from twigs import core as fc

from . import accounts

# peek: the branch you're on shows what's in it before you go in (a message, what it says)
LOOK = dict(colmax=40, glyphs='plain', ease=1 if os.environ.get('CDECK_CALM') else 0.3, peek=True)


def need(*tools):
    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        sys.exit(f"msgcanvas: needs {', '.join(missing)} (Debian: apt install isync notmuch msmtp pass)")


def configured():
    if not os.path.exists(accounts.NOTMUCH):
        sys.exit('msgcanvas: no accounts yet: `msgcanvas setup`')
    os.environ['NOTMUCH_CONFIG'] = accounts.NOTMUCH
    return accounts.load()[1]


def login(name, paste=False):
    found = {}
    for a in configured():                  # by its name or its key
        found[a.name] = found[a.key] = a
    if name not in found:
        sys.exit(f"msgcanvas: no account {name!r} (accounts: {', '.join(found) or 'none'})")
    a = found[name]
    if a.auth in accounts.OAUTH:
        need('gpg')
        os.makedirs(os.path.dirname(a.token), exist_ok=True)
        if os.path.exists(a.token):     # signing in again starts over: the file keeps the old flow
            os.remove(a.token)
        run = ['python3', accounts.OAUTH2, a.token, '--authorize', '--provider', a.auth, '--email', a.address]
        if a.auth == 'microsoft':
            # A browser, as Thunderbird signs in: universities often block device codes.
            # --paste: the browser can't reach this machine, so copy the code from its address bar.
            print(f'Signing in {a.address}: open the link it shows in your browser and sign in'
                  + (', then copy the code= part of the address it ends on.\n' if paste else '.\n')
                  + 'It asks for a client secret first: there isn\'t one, press Enter.\n')
            run += ['--authflow', 'authcode' if paste else 'localhostauthcode',
                    '--client-id', accounts.MICROSOFT_CLIENT]
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
    sys.exit(subprocess.call(['pass', 'insert', 'mail/' + a.key]))


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
    found = {}
    for a in configured():                  # by its name or its key; mbsync knows it by its key
        found[a.name] = found[a.key] = a
    if name and name not in found:
        sys.exit(f"msgcanvas: no account {name!r}")
    need('mbsync', 'notmuch')
    status = subprocess.call(['mbsync', '-c', accounts.MBSYNCRC, found[name].key if name else '-a'])
    indexed = subprocess.call(['notmuch', 'new'])
    sys.exit(status or indexed)


def state(name):
    d = os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state'), 'msgcanvas')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def calendar():
    """cal's calendar: the same calendars, feeds, reminders and what-ifs as `twigs cal`."""
    from twigs.apps.cal import cli
    return cli.calendar()


def has_calendar():
    from twigs.apps.cal import cli
    return cli.has_calendar()


def sections():
    """Emails and Messages, as they're set up (either, both, or neither): what msg adds to a canvas."""
    from . import chat, mail
    out = []
    if os.path.exists(accounts.NOTMUCH):
        found = configured()
        if found:
            defaults = accounts.load()[0]
            out.append(('Emails', mail.Mail(found, notify=defaults.get('notify', True), remember=state('mail-view.json'),
                                            beacon_days=defaults.get('beacon_days', 30), later=state('later.json'))))
    c = accounts.chat_settings()
    if c['enabled'] and shutil.which(c['nchat']) and chat.has_profiles(c['confdir'] or chat.confdir()):
        out.append(('Messages', chat.Chat(c['nchat'], c['confdir'], state('nchat-serve.log'),
                                          read_receipts=c['read_receipts'], typing=c['typing'], notify=c['notify'],
                                          remember=state('chat-view.json'))))
    return out


def browse(start):
    """Mail, chats and the calendar on one canvas; any may be missing."""
    from twigs.kit import hub
    found = sections() + ([('Calendar', calendar())] if has_calendar() else [])
    if not found:
        sys.exit('msgcanvas: nothing to show yet: `msgcanvas setup` for mail, `msgcanvas chat-setup` for chats, '
                 f'`calcanvas add` for a calendar')
    hub.open(found, start, intro=intro_wanted(), **LOOK)


def intro_wanted():
    return accounts.load()[0].get('intro', True) if os.path.exists(accounts.ACCOUNTS) else True


def send_due():
    """Send the drafts whose time has come, and leave (from cron, say, when msgcanvas isn't open)."""
    from . import mail
    p = mail.Mail(configured(), watch=0, notify=False, later=state('later.json'))
    p.due(wake=False)
    sent = [s for s in p.signals() if s[0] == 'status']
    for _, _, text, how in sent:
        print(('✓ ' if how == 'ok' else '✗ ') + text)
    sys.exit(1 if any(s[3] == 'failed' for s in sent) else 0)


def chat_setup():
    c = accounts.chat_settings()
    if not shutil.which(c['nchat']):
        sys.exit(f"msgcanvas: no {c['nchat']}: build nchat (github.com/3-mmc/nchat) or set [chat] nchat")
    print('nchat asks which service, then shows a QR code or asks for a code. Scan or type it,\n'
          'and when nchat opens on your chats, quit it (Ctrl-Q): msgcanvas uses it from then on.\n')
    sys.exit(subprocess.call([c['nchat'], '--setup'] + (['-d', c['confdir']] if c['confdir'] else [])))


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
    elif cmd == 'cal' and len(argv) <= 2:
        if not has_calendar():
            sys.exit('msgcanvas: no calendars yet: `calcanvas add NAME` (github.com/3-mmc/calcanvas)')
        fc.pick(argv[1] if len(argv) == 2 else '', provider=calendar(), **LOOK)
    elif cmd == 'chat-setup' and len(argv) == 1:
        chat_setup()
    elif cmd == 'import-thunderbird' and len(argv) == 1:
        import_thunderbird()
    elif cmd == 'login' and len(argv) in (2, 3) and argv[2:] in ([], ['--paste']):
        login(argv[1], paste=argv[2:] == ['--paste'])
    elif cmd == 'send-due' and len(argv) == 1:
        send_due()
    elif cmd == 'sync' and len(argv) <= 2:
        sync(argv[1] if len(argv) == 2 else None)
    elif len(argv) <= 1 and cmd not in ('login', 'sync', 'import-thunderbird', 'chat-setup', 'send-due', 'cal'):
        browse(cmd)
    else:
        sys.exit(__doc__.strip())


if __name__ == '__main__':
    main()
