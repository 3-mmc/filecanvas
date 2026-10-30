"""Mail accounts: one accounts.toml, and the mbsync, msmtp and notmuch configs made from it.

Each account syncs (mbsync) into ~/Mail/<name>/, notmuch indexes all of them, and msmtp sends.
The configs are generated into msgcanvas's own folder and passed to each tool (-c, -C,
NOTMUCH_CONFIG), so nothing in ~/.mbsyncrc, ~/.msmtprc or ~/.notmuch-config is touched.

Secrets never live here: a password is `pass show mail/<name>`, an OAuth token is a
gpg-encrypted file that neomutt's mutt_oauth2.py keeps fresh."""
import os, re, shlex, sys, tomllib

CONFIG = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'msgcanvas')
DATA = os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), 'msgcanvas')
ACCOUNTS = os.path.join(CONFIG, 'accounts.toml')
MBSYNCRC = os.path.join(CONFIG, 'mbsyncrc')
MSMTPRC = os.path.join(CONFIG, 'msmtprc')
NOTMUCH = os.path.join(CONFIG, 'notmuch-config')
OAUTH2 = '/usr/share/neomutt/oauth2/mutt_oauth2.py'
CERTS = '/etc/ssl/certs/ca-certificates.crt'
MARK = '# Made by msgcanvas from accounts.toml: edit that and run `msgcanvas setup`, not this.'

# Thunderbird's public client id: most university Microsoft 365 tenants already allow it.
MICROSOFT_CLIENT = '9e5f94bc-e8a4-4e73-b8be-63364c29d753'

# What each kind of account needs, before the account's own lines. `auth`: password (pass) or
# microsoft (OAuth). `patterns`: which folders mbsync fetches.
PRESETS = {
    'gmail': dict(imap_host='imap.gmail.com', smtp_host='smtp.gmail.com', smtp_port=587,
                  # All Mail and Important repeat every other folder; notmuch would only dedupe them
                  patterns='* "![Gmail]/All Mail" "![Gmail]/Important"'),
    'outlook': dict(imap_host='outlook.office365.com', smtp_host='smtp.office365.com', smtp_port=587,
                    auth='microsoft'),
    'seznam': dict(imap_host='imap.seznam.cz', smtp_host='smtp.seznam.cz', smtp_port=465),
    'imap': {},
}
BASE = dict(auth='password', imap_port=993, smtp_port=587, patterns='*', max_messages=2000)

TEMPLATE = '''# msgcanvas's mail accounts. After editing: `msgcanvas setup` makes the configs from this,
# `msgcanvas login NAME` stores a password or signs in, `msgcanvas sync` fetches mail.
#
# type: gmail (an app password: myaccount.google.com/apppasswords), outlook (Microsoft 365,
# signs in with OAuth), seznam, or imap (then give imap_host and smtp_host).
# Anything a type sets can be set per account: imap_host, imap_port, smtp_host, smtp_port,
# user (the login, if it isn't the address), patterns (mbsync's folder patterns).

[defaults]
real_name = "{real_name}"
maildir = "~/Mail"
max_messages = 2000     # newest messages kept per folder (the server keeps everything)

# [[account]]
# name = "gmail"                      # a short name: letters, digits, -
# address = "{email}"
# type = "gmail"

# [[account]]
# name = "uni"
# address = "you@university.example"
# type = "outlook"

# [[account]]
# name = "seznam"
# address = "you@seznam.cz"
# type = "seznam"

# [[account]]
# name = "other"
# address = "you@example.org"
# type = "imap"
# imap_host = "imap.example.org"
# smtp_host = "smtp.example.org"
'''


class Account:
    def __init__(self, entry, defaults):
        kind = entry.get('type', 'imap')
        if kind not in PRESETS:
            raise ValueError(f"{entry.get('name', '?')}: type {kind!r} isn't one of {', '.join(PRESETS)}")
        v = dict(BASE, **PRESETS[kind])
        v.update({k: defaults[k] for k in ('max_messages',) if k in defaults})
        v.update(entry)
        self.type = kind
        self.name = v.get('name', '')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', self.name):
            raise ValueError(f'{self.name!r}: a name is lower-case letters, digits and -')
        if '@' not in v.get('address', ''):
            raise ValueError(f'{self.name}: no address')
        for host in ('imap_host', 'smtp_host'):
            if not v.get(host):
                raise ValueError(f'{self.name}: type imap needs {host}')
        self.address = v['address']
        self.user = v.get('user', self.address)
        self.real_name = v.get('real_name', defaults.get('real_name', ''))
        self.imap_host, self.imap_port = v['imap_host'], int(v['imap_port'])
        self.smtp_host, self.smtp_port = v['smtp_host'], int(v['smtp_port'])
        self.auth, self.patterns = v['auth'], v['patterns']
        self.max_messages = int(v['max_messages'])
        self.maildir = os.path.join(os.path.expanduser(defaults.get('maildir', '~/Mail')), self.name)

    @property
    def token(self):
        return os.path.join(DATA, 'tokens', self.name + '.gpg')

    def secret(self, smtp=False):
        """The shell command that prints this account's password or a fresh OAuth token."""
        if self.auth == 'microsoft':
            return shlex.join(['python3', OAUTH2, self.token])
        return shlex.join(['pass', 'show', 'mail/' + self.name])


def load(path=ACCOUNTS):
    """(defaults, [Account]) from accounts.toml."""
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    defaults = data.get('defaults', {})
    accounts = [Account(e, defaults) for e in data.get('account', [])]
    names = [a.name for a in accounts]
    for n in set(names):
        if names.count(n) > 1:
            raise ValueError(f'{n}: two accounts have this name')
    return defaults, accounts


def quoted(text):
    """A value for mbsync's config: in double quotes, its own quotes escaped."""
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def mbsyncrc(accounts):
    out = [MARK, '']
    for a in accounts:
        out += [f'IMAPAccount {a.name}',
                f'Host {a.imap_host}',
                f'Port {a.imap_port}',
                f'User {a.user}',
                f'PassCmd {quoted(a.secret())}',
                'TLSType IMAPS',
                f'CertificateFile {CERTS}']
        if a.auth == 'microsoft':
            out.append('AuthMechs XOAUTH2')
        out += ['',
                f'IMAPStore {a.name}-far', f'Account {a.name}', '',
                f'MaildirStore {a.name}-near',
                f'Path {quoted(a.maildir + "/")}',
                f'Inbox {quoted(a.maildir + "/INBOX")}',
                'SubFolders Verbatim', '',
                f'Channel {a.name}',
                f'Far :{a.name}-far:',
                f'Near :{a.name}-near:',
                f'Patterns {a.patterns}',
                'Create Near',
                # Fetch only, for now: nothing msgcanvas does changes the server yet.
                'Sync Pull',
                'Expunge None',
                'SyncState *',
                'CopyArrivalDate yes']
        if a.max_messages:
            out += [f'MaxMessages {a.max_messages}', 'ExpireUnread yes']
        out.append('')
    return '\n'.join(out)


def msmtprc(accounts, log):
    out = [MARK, '', 'defaults', 'auth on', 'tls on', f'tls_trust_file {CERTS}', f'logfile {log}', '']
    for a in accounts:
        out += [f'account {a.name}',
                f'host {a.smtp_host}',
                f'port {a.smtp_port}',
                f'tls_starttls {"off" if a.smtp_port == 465 else "on"}',
                f'from {a.address}',
                f'user {a.user}',
                f'passwordeval {a.secret(smtp=True)}']
        if a.auth == 'microsoft':
            out.append('auth xoauth2')
        out.append('')
    if accounts:
        out.append(f'account default : {accounts[0].name}')
    return '\n'.join(out) + '\n'


def notmuch_config(defaults, accounts):
    root = os.path.expanduser(defaults.get('maildir', '~/Mail'))
    emails = [a.address for a in accounts]
    return '\n'.join([
        MARK, '',
        '[database]', f'path={root}', '',
        '[user]', f'name={defaults.get("real_name", "")}',
        f'primary_email={emails[0] if emails else ""}',
        f'other_email={";".join(emails[1:])}', '',
        # unread, flagged, replied, draft come from the maildir flags mbsync keeps
        '[new]', 'tags=inbox;', 'ignore=.mbsyncstate;.mbsyncstate.journal;.mbsyncstate.new;'
        '.mbsyncstate.lock;.uidvalidity', '',
        '[search]', 'exclude_tags=deleted;spam;', '',
        '[maildir]', 'synchronize_flags=true', ''])


def write(path, text):
    """Write a generated config, but never over a file msgcanvas didn't make."""
    if os.path.exists(path):
        with open(path) as f:
            if f.readline().rstrip('\n') != MARK:
                sys.exit(f"msgcanvas: {path} wasn't made by msgcanvas; move it away first")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)


def setup(real_name='', email=''):
    """Make the configs from accounts.toml, or start an accounts.toml to fill in."""
    if not os.path.exists(ACCOUNTS):
        os.makedirs(CONFIG, exist_ok=True)
        with open(ACCOUNTS, 'w') as f:
            f.write(TEMPLATE.format(real_name=real_name, email=email or 'you@gmail.com'))
        print(f'msgcanvas: started {ACCOUNTS}: add your accounts there, then run `msgcanvas setup` again')
        return []
    try:
        defaults, accounts = load()
    except (ValueError, tomllib.TOMLDecodeError) as e:
        sys.exit(f'msgcanvas: {ACCOUNTS}: {e}')
    state = os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state'), 'msgcanvas')
    os.makedirs(state, exist_ok=True)
    write(MBSYNCRC, mbsyncrc(accounts))
    write(MSMTPRC, msmtprc(accounts, os.path.join(state, 'msmtp.log')))
    write(NOTMUCH, notmuch_config(defaults, accounts))
    for a in accounts:
        os.makedirs(a.maildir, exist_ok=True)
    return accounts
