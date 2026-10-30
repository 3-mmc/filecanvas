"""Calendars you subscribe to by their address: an .ics feed. Google Calendar gives one for each
of yours (Settings → the calendar → Integrate calendar → "Secret address in iCal format") and for
the ones it shows you (holidays, a shared one); Outlook gives one when you publish a calendar.

They're listed in ~/.config/calcanvas/feeds.toml (only you can read it: the secret addresses are
as good as a password), and each is fetched into calendars/<key>/, where the canvas reads it
again when it changes. A fetch asks the server whether it's changed first (ETag,
Last-Modified), so fetching often costs little. Feeds are read-only: nothing goes back.

    [[feed]]
    name = "aaron@gmail.com"        # what the canvas calls it
    url = "https://calendar.google.com/calendar/ical/…/basic.ics"
    every = 15                      # minutes between fetches while the canvas is open (15)
    quiet = true                    # in the background: after your own things, fainter (holidays)
"""
import json, os, re, tomllib, urllib.error, urllib.parse, urllib.request

from . import cal

CONFIG = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'calcanvas')
FEEDS = os.path.join(CONFIG, 'feeds.toml')
SEEN = os.path.join(cal.STATE, 'feeds.json')     # each feed's ETag / Last-Modified, and when it came
EVERY = 15
TIMEOUT = 30


class Feed:
    def __init__(self, name, url, key=None, every=EVERY, quiet=False):
        self.name, self.url, self.every, self.quiet = name, url, every, quiet
        self.key = key or slug(name)

    def where(self):
        """The address without its secret: just the server (it's safe to show)."""
        return urllib.parse.urlsplit(address(self.url)).netloc


def public(url):
    """A public calendar's address (Google's holidays, the moon): one for the background."""
    return '/public/' in address(url)


def slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'calendar'


def address(url):
    return 'https://' + url[len('webcal://'):] if url.startswith('webcal://') else url


def load(path=FEEDS):
    try:
        with open(path, 'rb') as f:
            got = tomllib.load(f).get('feed', [])
    except FileNotFoundError:
        return []
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SystemExit(f'calcanvas: {path}: {e}')
    feeds, keys = [], set()
    for i, f in enumerate(got):
        if not f.get('url') or not f.get('name'):
            raise SystemExit(f'calcanvas: {path}: feed {i + 1} needs a name and a url')
        feed = Feed(f['name'], f['url'], f.get('key'), int(f.get('every', EVERY)), bool(f.get('quiet', False)))
        while feed.key in keys:
            feed.key += '-2'
        keys.add(feed.key)
        feeds.append(feed)
    return feeds


def save(feeds, path=FEEDS):
    """feeds.toml, written so only you can read it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    q = lambda v: json.dumps(v, ensure_ascii=False)      # a TOML basic string is a JSON string
    text = __doc__.split('\n\n')[0].replace('\n', '\n# ')
    out = [f'# {text}\n# (calcanvas add NAME, calcanvas remove NAME)\n']
    for f in feeds:
        out.append(f'[[feed]]\nname = {q(f.name)}\nurl = {q(f.url)}\n'
                   + (f'key = {q(f.key)}\n' if f.key != slug(f.name) else '')
                   + (f'every = {f.every}\n' if f.every != EVERY else '')
                   + ('quiet = true\n' if f.quiet else ''))
    tmp = path + '.new'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as fh:
        fh.write('\n'.join(out))
    os.replace(tmp, path)


def seen():
    try:
        with open(SEEN) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def remember(key, info):
    got = seen()
    got[key] = info
    os.makedirs(os.path.dirname(SEEN), exist_ok=True)
    with open(SEEN + '.new', 'w') as f:
        json.dump(got, f, indent=1)
    os.replace(SEEN + '.new', SEEN)


def folder(feed, calendars=None):
    return os.path.join(calendars or cal.CALENDARS, feed.key)


def fetch(feed, calendars=None, force=False):
    """Fetch `feed` into its folder: 'new' (it changed), 'same', or raise OSError saying why, never
    with its address (it's secret)."""
    import time
    before = {} if force else seen().get(feed.key, {})
    req = urllib.request.Request(address(feed.url), headers={'User-Agent': 'calcanvas'})
    if before.get('etag'):
        req.add_header('If-None-Match', before['etag'])
    if before.get('modified'):
        req.add_header('If-Modified-Since', before['modified'])
    out = folder(feed, calendars)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
            headers = r.headers
    except urllib.error.HTTPError as e:
        if e.code == 304 and os.path.exists(os.path.join(out, 'feed.ics')):
            remember(feed.key, dict(before, checked=time.time()))
            return 'same'
        raise OSError(f'{feed.where()} said {e.code} {e.reason}' +
                      (' (is the address still right? a reset secret address changes it)' if e.code in (401, 403, 404) else ''))
    except urllib.error.URLError as e:
        raise OSError(f"can't reach {feed.where()}: {getattr(e.reason, 'strerror', None) or e.reason}")
    except (TimeoutError, OSError) as e:
        raise OSError(f"can't reach {feed.where()}: {e.strerror or e}")
    text = body.decode('utf-8', errors='replace')
    if 'BEGIN:VCALENDAR' not in text[:2000]:
        raise OSError(f"{feed.where()} didn't send a calendar")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'displayname'), 'w') as f:
        f.write(feed.name + '\n')
    path = os.path.join(out, 'feed.ics')
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            unchanged = stamped(f.read()) == stamped(text)
    except OSError:
        unchanged = False
    if not unchanged:           # the same calendar again doesn't touch the file: nothing to redraw
        with open(path + '.new', 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(path + '.new', path)
    remember(feed.key, {'etag': headers.get('ETag'), 'modified': headers.get('Last-Modified'),
                        'checked': time.time()})
    return 'same' if unchanged else 'new'


def stamped(text):
    """`text` without its DTSTAMPs: Google stamps every fetch anew, though nothing else changed."""
    return re.sub(r'^DTSTAMP[:;].*$', '', text.replace('\r\n', '\n'), flags=re.M)


def count(feed, calendars=None):
    """How many events the feed's folder holds."""
    try:
        with open(os.path.join(folder(feed, calendars), 'feed.ics'), encoding='utf-8', errors='replace') as f:
            return f.read().count('BEGIN:VEVENT')
    except OSError:
        return 0
