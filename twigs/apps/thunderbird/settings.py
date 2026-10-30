"""Connection choices only; credentials and mail stay with Thunderbird."""
import json
import os
from pathlib import Path
from email.utils import parseaddr


def path():
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'twigs/thunderbird.json'


def load():
    try:
        data = json.loads(path().read_text())
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get('accounts'), list):
        raise ValueError('Invalid Thunderbird configuration; reconnect with twigs thunderbird connect')
    if not all(isinstance(a, str) for a in data['accounts']):
        raise ValueError('Invalid Thunderbird account selection')
    if not isinstance(data.get('include_existing', False), bool):
        raise ValueError('Invalid Thunderbird overlap preference')
    for key in ('node', 'connection_file'):
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            raise ValueError('Invalid Thunderbird '+key+' setting')
    return data


def save(data):
    dest = path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    os.replace(temp, dest)


def existing_addresses():
    from twigs.apps.msg import accounts
    try:
        _, configured = accounts.load()
    except FileNotFoundError:
        configured = []
    # A broken existing config must not silently disable duplicate detection.
    addresses = {a.address.strip().casefold() for a in configured}
    return addresses


def overlapping(account, addresses):
    return any(parseaddr(i.get('email', ''))[1].casefold() in addresses
               for i in account.get('identities', []))


def choose(accounts, requested=None, include_existing=False, addresses=None):
    addresses = existing_addresses() if addresses is None else addresses
    available = {a['id']: a for a in accounts}
    if requested:
        unknown = set(requested) - available.keys()
        if unknown:
            raise ValueError('Unknown Thunderbird account IDs: ' + ', '.join(sorted(unknown)))
    candidates = [a for a in accounts if not requested or a['id'] in requested]
    skipped = [a for a in candidates if overlapping(a, addresses) and not include_existing]
    selected = [a['id'] for a in candidates if a not in skipped]
    return selected, skipped
