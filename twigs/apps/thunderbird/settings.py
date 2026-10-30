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
    preferred = data.get('preferred_accounts', {})
    if not isinstance(preferred, dict) or any(
        not isinstance(k, str) or not isinstance(v, list) or not all(isinstance(a, str) for a in v)
        for k, v in preferred.items()
    ):
        raise ValueError('Invalid Thunderbird source preferences')
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
    candidates = [a for a in accounts if (a['id'] in requested if requested else a.get('type') != 'none')]
    skipped = [a for a in candidates if overlapping(a, addresses) and not include_existing]
    selected = [a['id'] for a in candidates if a not in skipped]
    return selected, skipped


def preferred_addresses(config=None):
    """Addresses explicitly routed through selected Thunderbird accounts, without touching mail config."""
    config = load() if config is None else config
    if not config:
        return set()
    return {address.casefold().strip()
            for ident, addresses in config.get('preferred_accounts', {}).items()
            if ident in config['accounts'] for address in addresses}


def manual_accounts(accounts):
    preferred = preferred_addresses()
    return [a for a in accounts if a.address.casefold().strip() not in preferred] if preferred else list(accounts)


def selection(accounts, config):
    return choose(accounts, config['accounts'],
                  config.get('include_existing', False) or bool(config.get('preferred_accounts')))
