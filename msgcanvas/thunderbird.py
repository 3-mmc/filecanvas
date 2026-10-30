"""Accounts from a Thunderbird profile's prefs.js: servers, logins and how each signs in, never
its saved passwords. Thunderbird on Windows is found from WSL through /mnt/c."""
import glob, json, os, re

# Thunderbird's authMethod numbers that matter here
OAUTH = 10

KINDS = (('imap.gmail.com', 'gmail'), ('outlook.office365.com', 'outlook'),
         ('imap.seznam.cz', 'seznam'), ('imap.mail.yahoo.com', 'yahoo'))
MAILBOX_DOMAINS = re.compile(r'(gmail|googlemail|email|post|seznam|yahoo|outlook|hotmail|live)\.')


def profiles():
    """prefs.js of every Thunderbird profile found, the most recently used first."""
    roots = [os.path.expanduser('~/.thunderbird'), os.path.expanduser('~/Library/Thunderbird/Profiles')]
    roots += glob.glob('/mnt/c/Users/*/AppData/Roaming/Thunderbird/Profiles')
    found = [p for r in roots for p in glob.glob(os.path.join(r, '*', 'prefs.js'))]
    return sorted(found, key=os.path.getmtime, reverse=True)


def prefs(path):
    out = {}
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = re.match(r'user_pref\("([^"]+)",\s*(.+)\);\s*$', line)
            if m:
                try:
                    out[m[1]] = json.loads(m[2])
                except ValueError:
                    out[m[1]] = m[2]
    return out


def short_name(address):
    """A name for an account: the mailbox at a mail provider (jane), else the organisation
    (uni for uni.ac.uk, college for college.edu)."""
    local, _, domain = address.lower().partition('@')
    part = local if MAILBOX_DOMAINS.match(domain) else domain.split('.')[0]
    return re.sub(r'[^a-z0-9]+', '-', part).strip('-') or 'mail'


def accounts(path):
    """[{name, address, type, auth, ...}] as accounts.toml takes them, in Thunderbird's order."""
    p = prefs(path)
    out, names = [], set()
    for acct in str(p.get('mail.accountmanager.accounts', '')).split(','):
        server = p.get(f'mail.account.{acct}.server')
        ident = str(p.get(f'mail.account.{acct}.identities', '')).split(',')[0]
        if not server or p.get(f'mail.server.{server}.type') != 'imap':
            continue     # Local Folders, POP, news, feeds
        s = lambda k: p.get(f'mail.server.{server}.{k}')
        host = s('hostname')
        address = p.get(f'mail.identity.{ident}.useremail') or s('userName')
        kind = next((k for h, k in KINDS if host == h), 'imap')
        entry = dict(name=short_name(address), address=address, type=kind)
        while entry['name'] in names:
            entry['name'] += '-2'
        names.add(entry['name'])
        if s('userName') and s('userName') != address:
            entry['user'] = s('userName')
        oauth = s('authMethod') == OAUTH
        if kind == 'gmail' and oauth:
            entry['auth'] = 'google'
        if kind == 'imap':
            smtp = p.get(f'mail.identity.{ident}.smtpServer')
            # no outgoing server set: smtp.* beside imap.*, else the same host
            guess = 'smtp.' + host[5:] if host.startswith('imap.') else host
            entry.update(imap_host=host, imap_port=s('port') or 993,
                         smtp_host=p.get(f'mail.smtpserver.{smtp}.hostname') or guess,
                         smtp_port=p.get(f'mail.smtpserver.{smtp}.port', 587))
        # Seznam's and Yahoo's OAuth are Thunderbird's own: here they take a password (Yahoo:
        # an app password), so they're left at the default.
        entry['_thunderbird'] = 'OAuth' if oauth else 'password'
        out.append(entry)
    return out


def toml(entries, source):
    """accounts.toml lines for `entries`."""
    lines = [f'\n# From Thunderbird ({source})']
    for e in entries:
        lines.append(f'\n[[account]]')
        for k, v in e.items():
            if not k.startswith('_'):
                lines.append(f'{k} = {json.dumps(v)}' if isinstance(v, str) else f'{k} = {v}')
    return '\n'.join(lines) + '\n'
