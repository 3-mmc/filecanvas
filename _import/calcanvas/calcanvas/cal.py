"""The calendar: months → days → what's on them. Where two things on a day collide, or where you
wonder about a day ("what if…", ⌃W), the day forks into its alternatives: you weigh them side by
side, each with what would follow it (⌃N then…), and keep one (⌃K); the rest fade, kept to come
back to.

Calendars are folders of .ics files (vdirsyncer's layout, or Google Calendar's export unzipped),
a folder or a file a calendar, under ~/.local/share/calcanvas/calendars/; feeds.py fetches the
ones you subscribe to there. What-ifs are kept here only (~/.local/state/calcanvas/whatif.json):
nothing is written to a calendar.

Repeating events: FREQ with INTERVAL, COUNT, UNTIL, BYDAY (MO, 2TU, -1FR), BYMONTHDAY, BYMONTH,
EXDATE and moved occurrences, laid out in the event's own time zone (9:00 in London stays 9:00
there across the clocks changing)."""
import colorsys, datetime as dt, json, os, re, threading, time, uuid, zlib
from zoneinfo import ZoneInfo

from . import treecanvas as fc

DATA = os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), 'calcanvas')
STATE = os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state'), 'calcanvas')
CALENDARS = os.path.join(DATA, 'calendars')
WHATIF = os.path.join(STATE, 'whatif.json')
REMINDED = os.path.join(STATE, 'reminded.json')
REMIND_AHEAD = 14       # days ahead whose events' reminders are looked at
NOTE_WIDTH = 60
NOTE_MAX = 700          # characters of a description shown; a long invitation's start
ROOT = '@cal'
# Each month its colour, each of its weeks a shade of it: a day's number and weekday wear them
MONTHS = {1: '#7fb2e5', 2: '#a08bd6', 3: '#5fbf8a', 4: '#9fd36b', 5: '#e0d45a', 6: '#f2b33d',
          7: '#e5735c', 8: '#e05a87', 9: '#c9a227', 10: '#ff8c2a', 11: '#b5835a', 12: '#5ab6c9'}
SHADES = (0.0, 0.14, -0.1, 0.2, -0.16, 0.07)     # lightness, week by week: neighbours apart
SOON = dt.timedelta(days=2)     # closer than this, an event's time comes first
SOON_COLOUR = 'sel_bg'
DAY = dt.timedelta(days=1)
# the lines mail-made events carry, which say nothing about the event
BOILERPLATE = ('g.co/calendar', 'extsrc=cal')


def local_zone():
    name = os.environ.get('TZ', '').lstrip(':')
    if not name:
        try:
            name = os.path.realpath('/etc/localtime').split('zoneinfo/', 1)[1]
        except (IndexError, OSError):
            name = ''
    try:
        return ZoneInfo(name)
    except Exception:
        return dt.datetime.now().astimezone().tzinfo


# -- reading .ics

def unescape(v):
    return re.sub(r'\\([\\;,nN])', lambda m: '\n' if m.group(1) in 'nN' else m.group(1), v)


def when(value, params, zone):
    """A DTSTART-like value: a date (all day), or a datetime in its own zone (`zone` if it has
    none: floating, or one only Windows knows, as Outlook's)."""
    if params.get('VALUE') == 'DATE' or re.fullmatch(r'\d{8}', value):
        return dt.date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    t = dt.datetime.strptime(value[:15], '%Y%m%dT%H%M%S')
    if value.endswith('Z'):
        return t.replace(tzinfo=dt.timezone.utc)
    try:
        return t.replace(tzinfo=ZoneInfo(params['TZID'].strip('"')))
    except Exception:
        return t.replace(tzinfo=zone)


def here(t, zone):
    """`t` on the clock here (a date stays a date)."""
    return t if all_day(t) else t.astimezone(zone)


def all_day(t):
    return type(t) is dt.date


def day_of(t):
    return t if all_day(t) else t.date()


class Event:
    """One occurrence: `uid` is unique among them (a repeat's has its day), `start` and `end` dates
    (all day, end not included) or datetimes here."""
    def __init__(self, uid, title, start, end=None, where='', note='', calendar='', rules=(), zone=None):
        self.uid, self.title, self.where, self.note, self.calendar = uid, title, where, note, calendar
        self.start = start
        self.end = end if end is not None and end >= start else (start + DAY if all_day(start) else start)
        self.rules, self.zone = rules, zone     # its reminders (reminders()), and the zone here

    @property
    def alarms(self):
        """When its reminders go off, here."""
        out = []
        for kind, v, related in self.rules:
            if kind == 'at':
                out.append(v)
                continue
            base = self.end if related == 'END' else self.start
            out.append((dt.datetime.combine(base, dt.time(), self.zone) if all_day(base) else base) + v)
        return out

    def over(self):
        """When it's over: its end, or for a day's (all day), the midnight after."""
        return dt.datetime.combine(self.end, dt.time(), self.zone) if all_day(self.end) else self.end

    @property
    def first(self):
        return day_of(self.start)

    @property
    def last(self):
        if all_day(self.start):
            return max(self.first, self.end - DAY)
        if self.end - self.start <= DAY:       # a night out (even a whole one) is the night it starts
            return self.first
        return (self.end - dt.timedelta(microseconds=1)).date()

    @property
    def span(self):
        """Over more than a day: a trip, a visit."""
        return self.last > self.first

    def at(self):
        return '' if all_day(self.start) else f'{self.start:%H:%M}'

    def __repr__(self):
        return f'Event({self.title!r}, {self.start})'


def components(text):
    """The VEVENTs in an .ics text, each {NAME: [(params, value)]}, its VALARMs (reminders) in
    ALARMS, each one the same."""
    out, cur, alarm = [], None, None
    for line in re.sub(r'\r?\n[ \t]', '', text).splitlines():
        head, _, value = line.partition(':')
        name, *params = head.split(';')
        name, what = name.upper(), value.upper()
        if name == 'BEGIN' and what == 'VEVENT':
            cur = {}
        elif name == 'END' and what == 'VEVENT':
            if cur is not None:
                out.append(cur)
            cur = None
        elif cur is not None and name == 'BEGIN' and what == 'VALARM':
            alarm = {}
        elif cur is not None and name == 'END' and what == 'VALARM':
            cur.setdefault('ALARMS', []).append(alarm or {})
            alarm = None
        elif alarm is not None:
            alarm.setdefault(name, []).append((dict(p.split('=', 1) for p in params if '=' in p), value))
        elif cur is not None:
            cur.setdefault(name, []).append((dict(p.split('=', 1) for p in params if '=' in p), value))
    return out


def reminders(c, zone):
    """A VEVENT's reminders as rules: ('at', when) or ('before', how long after its start (or its
    end, 'END'), negative before it). Emailed ones aren't ours to give."""
    out = []
    for a in c.get('ALARMS', ()):
        if 'TRIGGER' not in a or (a.get('ACTION') or [({}, '')])[0][1].upper() == 'EMAIL':
            continue
        p, v = a['TRIGGER'][0]
        if p.get('VALUE', '').upper() == 'DATE-TIME' or re.match(r'\d{8}T', v):
            if v[:4] >= '1990':     # Apple's "no reminder" is one set in 1976
                out.append(('at', here(when(v, p, zone), zone), None))
        else:
            out.append(('before', duration(v), p.get('RELATED', 'START').upper()))
    return out


WEEKDAYS = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')


def on_day(t, d):
    """`t` moved to the day `d`, at the same time on the clock of its zone."""
    return d if all_day(t) else t.replace(year=d.year, month=d.month, day=d.day)


def in_month(y, m, byday, bymonthday, default):
    """The days of month m in year y a rule picks: BYDAY ((n, weekday): the nth, from the end if
    negative, 0 every one), else BYMONTHDAY (negative from the end), else the `default` day."""
    first = dt.date(y, m, 1)
    days = (add_months(first, 1) - first).days
    if byday:
        out = []
        for n, wd in byday:
            hits = [first + DAY * i for i in range(days) if (first + DAY * i).weekday() == wd]
            out += hits if n == 0 else [hits[n - 1 if n > 0 else n]] if -len(hits) <= n <= len(hits) else []
        return out
    picks = bymonthday or [default]
    return [first + DAY * ((x if x > 0 else days + x + 1) - 1) for x in picks if 1 <= (x if x > 0 else days + x + 1) <= days]


def repeats(start, rule, zone, horizon):
    """The times an RRULE puts `start` on, in order, up to the day `horizon`."""
    r = dict(p.split('=', 1) for p in rule.upper().split(';') if '=' in p)
    freq, step = r.get('FREQ'), max(1, int(r.get('INTERVAL', 1)))
    if freq not in ('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'):    # hourly and the like: just the first
        yield start
        return
    count = int(r['COUNT']) if 'COUNT' in r else None
    until = day_of(here(when(r['UNTIL'], {}, zone), zone)) if 'UNTIL' in r else None
    byday = [(int(m[1] or 0), WEEKDAYS.index(m[2])) for m in re.finditer(r'([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)', r.get('BYDAY', ''))]
    bymonthday = [int(x) for x in r['BYMONTHDAY'].split(',')] if r.get('BYMONTHDAY') else []
    bymonth = [int(x) for x in r['BYMONTH'].split(',')] if r.get('BYMONTH') else []
    first = day_of(start)
    n = 0
    for k in range(0, 100000, step):
        if freq == 'DAILY':
            begins = first + DAY * k
            days = [begins] if (not byday or begins.weekday() in {w for _, w in byday}) and \
                (not bymonth or begins.month in bymonth) else []
        elif freq == 'WEEKLY':
            begins = first - DAY * first.weekday() + DAY * 7 * k        # its week's Monday
            days = [begins + DAY * w for w in sorted({w for _, w in byday})] if byday else [first + DAY * 7 * k]
        elif freq == 'MONTHLY':
            begins = add_months(first.replace(day=1), k)
            days = in_month(begins.year, begins.month, byday, bymonthday, first.day) \
                if not bymonth or begins.month in bymonth else []
        else:
            begins = dt.date(first.year + k, 1, 1)
            days = [d for mo in (bymonth or [first.month])
                    for d in in_month(begins.year, mo, byday if bymonth else [], bymonthday, first.day)]
        if begins > horizon or (until and begins > until):
            return
        for d in sorted(days):
            if d < first:
                continue
            if (until and d > until) or d > horizon or (count is not None and n >= count):
                return
            n += 1
            yield on_day(start, d)


def parse(text, calendar, zone, horizon):
    """The events in an .ics text, repeats laid out up to `horizon` (a date)."""
    out, moved = [], {}
    comps = components(text)
    for c in comps:          # a moved occurrence of a repeat replaces the one it moved
        if 'RECURRENCE-ID' in c and 'UID' in c:
            p, v = c['RECURRENCE-ID'][0]
            moved.setdefault(c['UID'][0][1], set()).add(day_of(when(v, p, zone)))
    for c in comps:
        get = lambda name: unescape(c[name][0][1]) if name in c else ''
        made = len(out)
        if 'DTSTART' not in c or get('STATUS').upper() == 'CANCELLED':
            continue
        p, v = c['DTSTART'][0]
        start = when(v, p, zone)
        if 'DTEND' in c:
            p, v = c['DTEND'][0]
            end = when(v, p, zone)
            if all_day(start) != all_day(end):      # a mixed pair: the end as the start is
                end = None
        elif 'DURATION' in c:
            end = start + duration(get('DURATION'))
        else:
            end = None
        uid = get('UID') or f'{calendar}:{get("SUMMARY")}:{start}'
        title, where, note = get('SUMMARY') or '(untitled)', get('LOCATION'), get('DESCRIPTION')
        if 'RRULE' in c and 'RECURRENCE-ID' not in c:
            skip = moved.get(uid, set()) | {day_of(when(v, p, zone)) for p, vs in c.get('EXDATE', [])
                                            for v in vs.split(',')}
            for t in repeats(start, c['RRULE'][0][1], zone, horizon):
                if day_of(t) not in skip:
                    out.append(Event(f'{uid}@{day_of(t)}', title, here(t, zone), end and here(t + (end - start), zone),
                                     where, note, calendar))
            for p, vs in c.get('RDATE', []):      # and the days it's added on
                for v in vs.split(','):
                    t = when(v, p, zone)
                    out.append(Event(f'{uid}@{day_of(t)}', title, here(t, zone), end and here(t + (end - start), zone),
                                     where, note, calendar))
        else:
            rid = ''
            if 'RECURRENCE-ID' in c:
                p, v = c['RECURRENCE-ID'][0]
                rid = f'@{day_of(when(v, p, zone))}'
            out.append(Event(uid + rid, title, here(start, zone), end and here(end, zone), where, note, calendar))
        rules = reminders(c, zone)
        for e in out[made:]:
            e.rules, e.zone = rules, zone
    return out


def duration(text):
    m = re.fullmatch(r'([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?', text.strip())
    if not m:
        return dt.timedelta()
    sign, w, d, h, mi, s = m.groups()
    t = dt.timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0),
                     seconds=int(s or 0))
    return -t if sign == '-' else t


def calendars(folder):
    """(name, [.ics files]) for each calendar in `folder`: a folder of them (its `displayname` file
    names it, as vdirsyncer's does), or one file."""
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name)
    except OSError:
        return []
    out = []
    for e in entries:
        if e.is_dir():
            files = sorted(os.path.join(e.path, f) for f in os.listdir(e.path) if f.endswith('.ics'))
            try:
                with open(os.path.join(e.path, 'displayname')) as f:
                    name = f.read().strip() or e.name
            except OSError:
                name = e.name
            if files:
                out.append((name, files))
        elif e.name.endswith('.ics'):
            out.append((e.name[:-4], [e.path]))
    return out


def has_calendars(folder=CALENDARS):
    return bool(calendars(folder))


# -- what-ifs

class Plans:
    """What-ifs, by day: {'2026-10-31': {'kept': an alternative's id or None, 'plans': [{'id',
    'title', 'instead_of': [event uids], 'steps': [text]}]}}, in a JSON file."""
    def __init__(self, path):
        self.path = path
        try:
            with open(path) as f:
                self.days = json.load(f).get('days', {})
        except (TypeError, OSError, ValueError):
            self.days = {}

    def get(self, day):
        return self.days.get(day.isoformat(), {'kept': None, 'plans': []})

    def edit(self, day):
        return self.days.setdefault(day.isoformat(), {'kept': None, 'plans': []})

    def save(self):
        self.days = {d: s for d, s in self.days.items() if s['plans'] or s['kept']}
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + '.new'
        with open(tmp, 'w') as f:
            json.dump({'days': self.days}, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)

    def find(self, day, pid):
        return next((p for p in self.get(day)['plans'] if p['id'] == pid), None)


class Reminded:
    """Which reminders have been told of ('told') and looked at ('seen'), by event and when it
    went off, in a JSON file: none is told twice, even across starts."""
    def __init__(self, path):
        self.path = path
        try:
            with open(path) as f:
                self.got = json.load(f)
        except (TypeError, OSError, ValueError):
            self.got = {}

    def get(self, uid, at):
        return self.got.get(f'{uid}|{at.isoformat()}')

    def put(self, uid, at, what):
        self.got[f'{uid}|{at.isoformat()}'] = what

    def save(self):
        if not self.path:
            return
        cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)).isoformat()
        self.got = {k: v for k, v in self.got.items() if k.rsplit('|', 1)[1] >= cut[:10]}    # two months' worth
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path + '.new', 'w') as f:
            json.dump(self.got, f, indent=1)
        os.replace(self.path + '.new', self.path)


def reminder_text(e, now):
    """What a reminder's notification says: when, and where."""
    if all_day(e.start):
        left = (e.first - now.date()).days
        when = 'today' if left <= 0 else 'tomorrow' if left == 1 else dated(e.first)
    else:
        left = e.start - now
        when = (f'now, until {e.end:%H:%M}' if left <= dt.timedelta() else
                f'in {max(1, int(left.total_seconds() // 60))} min, at {e.start:%H:%M}' if left < dt.timedelta(hours=1) else
                f'at {e.start:%H:%M}' if e.first == now.date() else f'{dated(e.first)} at {e.start:%H:%M}')
    return when + (f' · {e.where}' if e.where else '')


def clashes(events):
    """The timed events among `events` that overlap another."""
    timed = [e for e in events if not all_day(e.start)]
    return {e.uid for e in timed for f in timed if e is not f and e.start < f.end and f.start < e.end}


# -- the provider

class Calendar(fc.Provider):
    """Months → days → what's on them, and the forks of what-ifs. `KINDS` are the node kinds it
    answers for."""
    KINDS = ('calendar', 'month', 'day', 'span', 'event', 'fork', 'alt', 'step', 'detail')

    def __init__(self, folder=CALENDARS, plans=None, today=None, watch=2.0, zone=None, now=None, feeds=(),
                 fetch=None, quiet=None, notify=None, reminded=None, remind=None):
        self.folder, self.zone = folder, zone or local_zone()
        # calendars in the background (holidays, the moon): after your own, fainter, never leading a day
        self.quiet = set(quiet if quiet is not None else (f.name for f in feeds if getattr(f, 'quiet', False)))
        self.fixed_now = now or (dt.datetime.combine(today, dt.time(12), self.zone) if today else None)
        self.plans = Plans(plans)
        self.notify = notify                    # notify(title, text): a desktop notification
        # minutes before a calendar's timed events with no reminder of their own (Google's default
        # reminder isn't in its feeds), by calendar
        self.remind_by = remind if remind is not None else \
            {f.name: f.remind for f in feeds if getattr(f, 'remind', None)}
        self.reminded = Reminded(reminded)
        self.lock = threading.Lock()
        self.shown_days, self.shown_spans = {}, {}   # where each day and era is on the canvas, by path
        self.load()
        if watch:
            threading.Thread(target=self.watch, args=(watch,), daemon=True).start()
        if feeds and fetch:
            threading.Thread(target=self.keep_fetching, args=(feeds, fetch), daemon=True).start()

    def keep_fetching(self, feeds, fetch):
        """Fetch each feed now, then every so often (its `every`), a chip in the bar while it does;
        what changes, watch() sees and shows."""
        due = {f.key: 0.0 for f in feeds}
        while True:
            for f in feeds:
                if time.time() < due[f.key]:
                    continue
                key = 'feed:' + f.key
                self.status(key, f.name, 'busy')
                try:
                    self.status(key, f.name, 'gone' if fetch(f) == 'same' else 'ok')
                except OSError as e:
                    self.status(key, f'{f.name}: {e}', 'failed')
                due[f.key] = time.time() + f.every * 60
            time.sleep(10)

    def now(self):
        return self.fixed_now or dt.datetime.now(self.zone)

    def today(self):
        return self.now().date()

    def soon(self, e):
        """How long until `e`, when it's close ('in 3h', 'tomorrow', 'now'); None when it isn't."""
        if e.calendar in self.quiet:
            return None
        now = self.now()
        if all_day(e.start):
            days = (e.first - now.date()).days
            return 'today' if e.first <= now.date() <= e.last else 'tomorrow' if days == 1 else \
                'in 2 days' if days == 2 else None
        if e.start <= now < e.end:
            return 'now'
        left = e.start - now
        if not dt.timedelta() <= left <= SOON:
            return None
        days = (e.first - now.date()).days
        if left < dt.timedelta(hours=1):
            return f'in {max(1, left.seconds // 60)}m'
        return f'in {left.seconds // 3600}h' if days == 0 else 'tomorrow' if days == 1 else f'in {days} days'

    def title(self, e):
        """An event's name, its time first when it's close."""
        return f'{e.at()} {e.title}' if e.at() and self.soon(e) else e.title

    def leads(self, e):
        return bool(e and not all_day(e.start) and self.soon(e))

    def loading(self):
        n = len(self.names)
        return f'{n} calendar{"s" if n != 1 else ""}', True

    def signature(self):
        sig = []
        for _, files in calendars(self.folder):
            for f in files:
                try:
                    sig.append((f, os.stat(f).st_mtime_ns))
                except OSError:
                    pass
        return sig, self.today(), self.close()      # a new day, or something coming close: labels move

    def near(self):
        """Your timed events from yesterday to three days on: those a countdown can be about."""
        today = self.today()
        return [e for d in range(-1, 4) for e in self.by_day.get(today + d * DAY, ())
                if not all_day(e.start) and e.calendar not in self.quiet]

    def close(self):
        """Which events are close now: their time leads their label, so when this changes, the
        labels are made again."""
        return frozenset(e.uid for e in self.near() if self.soon(e))

    def next_change(self):
        """When a countdown in the column next ticks over ('in 31m' to 'in 30m', 'in 3h' to 'in
        2h'), something ends or comes within two days, or the day turns: the canvas draws then,
        and sleeps till then."""
        now = self.now()
        at = [dt.datetime.combine(now.date() + DAY, dt.time(), self.zone)]
        for e in self.near():
            left = e.start - now
            if dt.timedelta() < left <= SOON:
                step = 60 if left < dt.timedelta(hours=1) else 3600
                at.append(now + dt.timedelta(seconds=left.total_seconds() % step or step))
            elif left > SOON:
                at.append(e.start - SOON)
            elif now < e.end:
                at.append(e.end)
        at += [a for e in self.ahead() for a in e.alarms if a > now]     # a reminder going off
        return min(at).timestamp()

    def watch(self, every):
        seen = self.signature()
        while True:
            self.remind()
            time.sleep(every)
            now = self.signature()
            if now != seen:         # synced, or a new day: today has moved
                seen = now
                self.load()
                self.invalidate()

    def load(self):
        horizon = self.today().replace(year=self.today().year + 3)
        events, names = [], []
        for name, files in calendars(self.folder):
            names.append(name)
            for path in files:
                try:
                    with open(path, encoding='utf-8', errors='replace') as f:
                        events += parse(f.read(), name, self.zone, horizon)
                except (OSError, ValueError):
                    continue
        by_day, spans = {}, []
        for e in events:
            if not e.rules and e.calendar in self.remind_by and not all_day(e.start):
                e.rules, e.zone = [('before', -dt.timedelta(minutes=self.remind_by[e.calendar]), 'START')], self.zone
            if e.span:
                spans.append(e)
            else:
                by_day.setdefault(e.first, []).append(e)
        for es in by_day.values():
            es.sort(key=lambda e: (e.calendar in self.quiet, not all_day(e.start),
                                   e.start if not all_day(e.start) else dt.datetime.min.replace(tzinfo=self.zone), e.title))
        with self.lock:
            self.names, self.by_day, self.spans = names, by_day, sorted(spans, key=lambda e: (e.first, e.title))

    # -- reminders
    def ahead(self):
        """Your events from yesterday to REMIND_AHEAD days on, eras too: those a reminder can be
        going off for."""
        today = self.today()
        days = [e for d in range(-1, REMIND_AHEAD + 1) for e in self.by_day.get(today + d * DAY, ())]
        spans = [e for e in self.spans if e.first <= today + REMIND_AHEAD * DAY and e.last >= today - DAY]
        return [e for e in days + spans if e.calendar not in self.quiet and e.rules]

    def going_off(self, e):
        """When `e`'s latest reminder went off, if one has and `e` isn't over and hasn't been
        looked at since; else None."""
        now = self.now()
        gone = [a for a in e.alarms if a <= now]
        if not gone or now >= e.over():
            return None
        last = max(gone)
        return None if self.reminded.get(e.uid, last) == 'seen' else last

    def remind(self):
        """Tell of each reminder that's gone off and hasn't been told (a desktop notification, its
        day flashing), once: even across starts. From watch()'s thread."""
        told = []
        with self.lock:
            for e in self.ahead():
                last = self.going_off(e)
                if last is not None and self.reminded.get(e.uid, last) is None:
                    self.reminded.put(e.uid, last, 'told')
                    told.append(e)
        if not told:
            return
        self.reminded.save()
        for e in told:
            if self.notify:
                self.notify(e.title, reminder_text(e, self.now()))
            for path in self.shown_days.get(e.first, ()):
                self.pulse(path)
        self.invalidate()

    def beacon(self, node):
        """Something with a reminder gone off, not looked at yet: white on red, blinking from when it
        went off; and the day it's on, so it shows in the month."""
        d = node.data or {}
        if node.kind in ('event', 'span'):
            evs = [d['event']]
        elif node.kind == 'alt':
            evs = [d['what']] if d['kind'] == 'event' else []
        elif node.kind == 'day':
            evs = [w for k, w in d['items'] if k == 'event'] + \
                [a[2] for k, w in d['items'] if k == 'fork' for a in w if a[1] == 'event']
        else:
            return None
        got = [t for t in map(self.going_off, evs) if t is not None]
        return max(got).timestamp() if got else None

    def focus(self, node):
        """Looking at something whose reminder went off: it's seen, and stops."""
        d = node.data or {}
        e = d.get('event') if node.kind in ('event', 'span') else d['what'] if node.kind == 'alt' and \
            d['kind'] == 'event' else d['items'][0][1] if node.kind == 'day' and len(d['items']) == 1 and \
            d['items'][0][0] == 'event' else None
        if e is not None and (last := self.going_off(e)) is not None:
            self.reminded.put(e.uid, last, 'seen')
            self.reminded.save()

    # -- what's on a day
    def fork(self, day):
        """The day's alternatives, [(id, kind, Event | plan | None)], or None: the events that
        clash, those a what-if would replace, the what-ifs, and with no event among them, keeping
        the day free."""
        events = self.by_day.get(day, [])
        state = self.plans.get(day)
        named = {u for p in state['plans'] for u in p['instead_of']}
        clash = clashes(events)
        evs = [e for e in events if e.uid in clash or e.uid in named]
        alts = [('ev:' + e.uid, 'event', e) for e in evs] + [(p['id'], 'plan', p) for p in state['plans']]
        if not state['plans'] and len(evs) < 2:
            return None
        if not evs:         # against the day as it would be: free, or with what's on it
            alts.insert(0, ('free', 'free', 'as it is' if events else 'keep the day free'))
        return alts

    def kept(self, day, alts):
        k = self.plans.get(day)['kept']
        return k if any(a[0] == k for a in alts) else None

    def items(self, day):
        """What's on `day`, in order: ('event', Event) and one ('fork', alternatives)."""
        alts = self.fork(day)
        inside = {a[2].uid for a in alts or () if a[1] == 'event'}
        out = [('event', e) for e in self.by_day.get(day, []) if e.uid not in inside]
        if alts:
            starts = [a[2].start for a in alts if a[1] == 'event']
            at = next((i for i, (_, e) in enumerate(out) if not all_day(e.start) and starts
                       and e.start > min(starts)), len(out))
            out.insert(at, ('fork', alts))
        return out

    def months(self):
        today = self.today()
        days = list(self.by_day) + [s.first for s in self.spans] + \
            [dt.date.fromisoformat(d) for d in self.plans.days]
        lo = max(min(days, default=today), add_months(today, -6)).replace(day=1)
        hi = min(max(days + [add_months(today, 3)]), add_months(today, 24)).replace(day=1)
        out, m = [], min(lo, today.replace(day=1))
        while m <= hi:
            out.append(m)
            m = add_months(m, 1)
        return out

    def month_rows(self, m):
        return self.rows(m, add_months(m, 1))

    def rows(self, lo, end, era=None):
        """The rows from `lo` until `end`: ('span', Event) for what runs over days, ('day', date,
        items). In an era (a span), the spans that start within it."""
        spans = [s for s in self.spans if s.uid != era.uid and lo <= s.first < end] if era else \
            [s for s in self.spans if s.first < end and s.last >= lo]
        rows = [((max(s.first, lo), 0), ('span', s)) for s in spans]
        days = {d for d in self.by_day if lo <= d < end} | \
            {d for d in map(dt.date.fromisoformat, self.plans.days) if lo <= d < end}
        today = self.today()
        if lo <= today < end:
            days.add(today)
        for d in days:
            rows.append(((d, 1), ('day', d, self.items(d))))
        return [r for _, r in sorted(rows, key=lambda r: r[0])]

    def row_nodes(self, parent, rows):
        out = []
        for row in rows:
            if row[0] == 'span':
                e = row[1]
                n = fc.Node(f'{parent.path}/s:{e.uid}', f'{span_dates(e)}  {e.title}', True, parent, 'span',
                            data={'event': e})
                self.shown_spans[n.path] = e
            else:
                _, day, items = row
                n = fc.Node(f'{parent.path}/{day}', self.day_label(day, items), bool(items), parent, 'day',
                            data={'day': day, 'items': items, 'lead': self.day_leads(day, items)})
                self.shown_days.setdefault(day, set()).add(n.path)
            out.append(n)
        return out

    # -- the tree
    def root(self):
        return fc.Node(ROOT, 'calendar', True, source=self, kind='calendar')

    def children(self, node):
        k, d = node.kind, node.data or {}
        with self.lock:
            if k == 'root':
                return [fc.Node(ROOT, 'calendar', True, node, 'calendar')]
            if k == 'calendar':
                return [fc.Node(f'{ROOT}/{m:%Y-%m}', month_name(m, self.today()), True, node, 'month',
                                data={'month': m}) for m in self.months()]
            if k == 'month':
                return self.row_nodes(node, self.month_rows(d['month']))
            if k == 'span':     # an era: when it is, then what happens in it
                e = d['event']
                return self.details(node, e) + self.row_nodes(node, self.rows(e.first, e.last + DAY, e))
            if k == 'day':
                items = d['items']
                if len(items) == 1:     # a day with one thing on it is that thing
                    kind, what = items[0]
                    return self.alternatives(node, d['day'], what) if kind == 'fork' else self.details(node, what, d['day'])
                return [self.item(node, d['day'], kind, what) for kind, what in items]
            if k == 'fork':
                return self.alternatives(node, d['day'], d['alts'])
            if k == 'event':
                return self.details(node, d['event'], d.get('day'))
            if k == 'alt':
                out = self.details(node, d['what'], d['day']) if d['kind'] == 'event' else []
                if d['kind'] == 'plan':
                    out += [fc.Node(f'{node.path}/then{i}', f'then  {s}', False, node, 'step',
                                    data={'day': d['day'], 'plan': d['what']['id'], 'step': i})
                            for i, s in enumerate(d['what']['steps'])]
                return out
        return []

    def item(self, parent, day, kind, what):
        if kind == 'fork':
            return fc.Node(f'{parent.path}/⑂', self.fork_label(day, what), True, parent, 'fork',
                           data={'day': day, 'alts': what})
        return fc.Node(f'{parent.path}/{what.uid}', self.title(what), True, parent, 'event',
                       data={'event': what, 'day': day, 'lead': self.leads(what)})

    def alternatives(self, parent, day, alts):
        out = []
        for aid, kind, what in alts:
            if kind == 'event':
                name = self.title(what)
            elif kind == 'plan':
                name = what['title']
            else:
                name = what
            out.append(fc.Node(f'{parent.path}/{aid}', name, kind == 'event' or (kind == 'plan' and bool(what['steps'])),
                               parent, 'alt', data={'day': day, 'id': aid, 'kind': kind, 'what': what, 'alts': alts,
                                                    'lead': kind == 'event' and self.leads(what)}))
        return out

    def details(self, parent, e, day=None):
        rows = [('time', when_text(e))]
        if e.where:
            rows.append(('where', e.where))
        note = clean(e.note)
        if note:
            rows.append(('note', note))
        if len(self.names) > 1:
            rows.append(('from', e.calendar))
        return [fc.Node(f'{parent.path}/{what}', text, False, parent, 'detail', data={'what': what, 'day': day})
                for what, text in rows]

    # -- labels
    def fork_label(self, day, alts):
        k = self.kept(day, alts)
        if k:
            a = next(a for a in alts if a[0] == k)
            return self.title(a[2]) if a[1] == 'event' else alt_title(a)
        room = max(10, 34 // len(alts))      # each its share, so the last isn't lost
        lead = self.first_event(alts)
        return (f'{lead.at()} ' if self.leads(lead) else '') + ' or '.join(short(alt_title(a), room) for a in alts)

    def first_event(self, alts):
        evs = [a[2] for a in alts if a[1] == 'event']
        return min(evs, key=lambda e: e.start) if evs else None

    def lead_event(self, day, items):
        """The event a day's label starts with, if it's timed: an event, or a fork's first."""
        if not items:
            return None
        kind, what = self.lead(items)
        if kind == 'fork':
            k = self.kept(day, what)
            got = next((a for a in what if a[0] == k), None) if k else None
            return got[2] if got and got[1] == 'event' else None if got else self.first_event(what)
        return what

    def lead(self, items):
        """What a day's label starts with: a fork, being what's to decide, else the first of your
        own, else the first thing."""
        return next((i for i in items if i[0] == 'fork'), None) or \
            next((i for i in items if i[1].calendar not in self.quiet), items[0])

    def background(self, items):
        """Whether all that's on a day is from quiet calendars."""
        return bool(items) and all(k == 'event' and w.calendar in self.quiet for k, w in items)

    def day_leads(self, day, items):
        return self.leads(self.lead_event(day, items))

    def day_label(self, day, items):
        head = f'{day.day:>2} {day:%a}'
        if not items:
            return head + ('  today' if day == self.today() else '')
        kind, what = self.lead(items)
        first = self.fork_label(day, what) if kind == 'fork' else self.title(what)
        return f'{head}  {first}' + (f'  +{len(items) - 1}' if len(items) > 1 else '')

    def tint(self, node):
        """A day's number and weekday in its month's colour, the shade of its week; a close
        event's time in the choice's colour; a month's name in its own."""
        d = node.data or {}
        if node.kind == 'month':
            return [(len(node.name), MONTHS[d['month'].month], True)]
        out = []
        if node.kind == 'day':
            c = shade(d['day'])
            out = [(2, c, True), (4, c, False)] + ([(2, c, False)] if d['lead'] else [])
        if d.get('lead'):
            out.append((5, SOON_COLOUR, True))
        return out

    def label(self, node):
        return node.name

    def undecided(self, node):
        """The forks under `node` not decided yet (today's and later, for a month)."""
        d = node.data or {}
        if node.kind == 'month':
            end = add_months(d['month'], 1)
            days = {x for x in self.by_day if d['month'] <= x < end} | \
                {x for x in map(dt.date.fromisoformat, self.plans.days) if d['month'] <= x < end}
            return sum(1 for x in days if x >= self.today() and (a := self.fork(x)) and not self.kept(x, a))
        alts = self.fork_of(node)
        return 1 if alts and not self.kept(d['day'], alts) else 0

    def fork_of(self, node):
        """The alternatives `node` opens onto, if it's a fork (or a day that is one)."""
        d = node.data or {}
        if node.kind == 'fork':
            return d['alts']
        if node.kind == 'day' and len(d['items']) == 1 and d['items'][0][0] == 'fork':
            return d['items'][0][1]
        return None

    def colour(self, node):
        d = node.data or {}
        if node.kind == 'day' and d['day'] == self.today():
            return 'path', True
        if node.kind == 'detail' and d['what'] in ('time', 'from'):
            return 'stub', False
        if node.kind == 'step':
            return 'stub', False
        return None

    def chip(self, node):
        """Spans (a trip, a visit) as bars of their own colour, as a month's grid draws them."""
        if node.kind == 'span':
            return 'person_fg', f'person{zlib.crc32(node.data["event"].title.encode()) % fc.PEOPLE}'
        return None

    def width(self, node):
        return NOTE_WIDTH if node.kind == 'detail' and node.data['what'] == 'note' else None

    def count(self, node):
        d = node.data or {}
        if node.kind == 'month':
            n = sum(1 for r in self.month_rows(d['month']) if r[0] == 'span' or (r[2] and not self.background(r[2])))
            return (str(n), 'stub', False) if n else None
        if node.kind == 'span':
            e, today = d['event'], self.today()
            n = (e.last - e.first).days + 1
            if e.first <= today <= e.last:          # an era you're in: how far into it
                return f'day {(today - e.first).days + 1} of {n}', SOON_COLOUR, True
            return f'{n} days', 'stub', False
        if node.kind == 'day':
            evs = [w for k, w in d['items'] if k == 'event'] + \
                [a[2] for k, w in d['items'] if k == 'fork' for a in w if a[1] == 'event']
            close = [(e.start if not all_day(e.start) else None, self.soon(e)) for e in evs if self.soon(e)]
            if close:           # the first thing that's close: how long until it
                return min(close, key=lambda c: (c[0] is not None, c[0] or 0))[1], SOON_COLOUR, True
            if len(d['items']) == 1 and d['items'][0][0] == 'event' and d['items'][0][1].at():
                return d['items'][0][1].at(), 'stub', False
            return None
        if node.kind == 'event' or (node.kind == 'alt' and d['kind'] == 'event'):
            e = d['event'] if node.kind == 'event' else d['what']
            if self.soon(e):
                return self.soon(e), SOON_COLOUR, True
            return (e.at(), 'stub', False) if e.at() else None
        return None

    def mark(self, node):
        d = node.data or {}
        if node.kind in ('month', 'day', 'fork'):
            if self.undecided(node):
                return 'git_conflict', '⑂', 'Y'
            if node.kind != 'month' and self.fork_of(node):
                return 'git_staged', '⑂', 'Y'
        if node.kind == 'alt':
            k = self.kept(d['day'], d['alts'])
            if k == d['id']:
                return 'git_staged', '✓', '+'
            if k:
                return 'git_untracked', '·', '-'
            return ('git_untracked', '?', '?') if d['kind'] == 'plan' else ('git_dirty', '○', 'o')
        return None

    def mark_before(self, node):
        return node.kind == 'alt'

    def dim(self, node):
        d = node.data or {}
        if node.kind == 'alt':
            k = self.kept(d['day'], d['alts'])
            return 0.45 if k and k != d['id'] else 1.0
        if node.kind == 'month' and add_months(d['month'], 1) <= self.today():
            return 0.7
        quiet = 0.6 if (node.kind == 'day' and self.background(d['items'])) or \
            (node.kind == 'event' and d['event'].calendar in self.quiet) else 1.0
        if node.kind == 'day' and d['day'] < self.today():
            return 0.7 * quiet
        return quiet

    def line(self, node):
        d = node.data or {}
        if node.kind == 'alt':
            k = self.kept(d['day'], d['alts'])
            return 'git_staged' if k == d['id'] else None
        if node.kind in ('fork', 'day') and self.undecided(node):
            return 'git_conflict'
        return None

    def start_at(self, node):
        d = node.data or {}
        if node.kind == 'calendar':
            ms = self.months()
            t = self.today().replace(day=1)
            return next((i for i, m in enumerate(ms) if m == t), 0)
        if node.kind == 'month' and d['month'] <= self.today() < add_months(d['month'], 1):
            rows = self.month_rows(d['month'])
            return next((i for i, r in enumerate(rows) if r[0] == 'day' and r[1] >= self.today()), 0)
        if node.kind == 'span':     # an era: on what's next in it, or its first
            e = d['event']
            rows = self.rows(e.first, e.last + DAY, e)
            head = len(self.details(node, e))
            return head + next((i for i, r in enumerate(rows) if r[0] == 'day' and r[1] >= self.today()), 0) \
                if rows else 0
        return 0

    def locate(self, start):
        """`2026-12`: that month; nothing: this one, on today."""
        today = self.today()
        m = re.fullmatch(r'(\d{4})-(\d{1,2})', start or '')
        month = dt.date(int(m[1]), int(m[2]), 1) if m else today.replace(day=1)
        ids = [f'{ROOT}/{month:%Y-%m}']
        if month == today.replace(day=1):
            ids.append(f'{ids[0]}/{today}')
        return ids, ''

    def pane_for(self, node):
        return False

    def bar(self, node, plain):
        names, n = [], node
        while n.parent and n.kind != 'calendar':
            names.append(short(n.name, 40)); n = n.parent
        names.reverse()
        segs = [('calendar', 'home_fg', 'home_bg', False)] + [
            (t, 'cwd_fg' if i == len(names) - 1 else 'seg_fg', 'seg_bg', i == len(names) - 1)
            for i, t in enumerate(names)]
        tail = []
        open_ = sum(1 for d in map(dt.date.fromisoformat, set(map(str, self.by_day)) | set(self.plans.days))
                    if d >= self.today() and (a := self.fork(d)) and not self.kept(d, a))
        if open_:
            tail.append((f'{open_} to decide', 'repo_dirty_fg', 'repo_dirty_bg', False))
        if node.kind in ('alt', 'step', 'fork'):
            tail.append(('what-ifs stay here', 'bg', 'filter', False))
        return segs, tail

    def choose(self, node):
        return None if node.is_dir else fc.STAY

    # -- what-ifs
    def actions(self, node):
        d = node.data or {}
        if node.kind not in self.KINDS or node.kind == 'calendar':
            return []
        out = [('\x17', 'whatif', '⌃W what if…')]
        if node.kind == 'alt':
            k = self.kept(d['day'], d['alts'])
            out.append(('\x0b', 'keep', '⌃K undecide' if k == d['id'] else '⌃K keep this'))
        if node.kind == 'alt' and d['kind'] == 'plan' or node.kind == 'step':
            out += [('\x0e', 'then', '⌃N then…'), ('\x04', 'drop', '⌃D throw away')]
        if node.kind == 'alt' and d['kind'] == 'plan':
            out.append(('\x05', 'rename', '⌃E rename'))
        return out

    def touch(self, node, day=None):
        """What `node` shows has changed: it and every branch above it, up to its month, again;
        and `day` wherever else it's shown (in its month, in an era), with what holds it."""
        while node is not None and node.kind != 'calendar':
            self.invalidate(node.path)
            node = node.parent
        if day is None:
            return
        self.invalidate(f'{ROOT}/{day:%Y-%m}')
        for path in self.shown_days.get(day, ()):
            self.invalidate(path)
            self.invalidate(path.rsplit('/', 1)[0])
        for path, e in self.shown_spans.items():
            if e.first <= day <= e.last:
                self.invalidate(path)

    def act(self, node, name, canvas):
        d = node.data or {}
        day = d.get('day')
        if name == 'whatif':
            self.what_if(node, canvas)
        elif name == 'keep':
            state = self.plans.edit(day)
            title = next(alt_title(a) for a in d['alts'] if a[0] == d['id'])
            if state['kept'] == d['id']:
                state['kept'] = None
                canvas.say(f'{short(title)}: undecided again')
            else:
                state['kept'] = d['id']
                for a in d['alts']:     # what's let go turns to dust in the bar
                    if a[0] != d['id']:
                        self.status('drop:' + a[0], short(alt_title(a)), 'gone')
                canvas.say(f'kept {short(title)} (here only: your calendar is as it was)')
            self.plans.save()
            self.touch(node.parent, day)
            self.pulse(node.parent.path)
        elif name == 'then':
            pid = d['what']['id'] if node.kind == 'alt' else d['plan']
            target = node if node.kind == 'alt' else node.parent

            def then(text):
                if text.strip():
                    self.plans.find(day, pid)['steps'].append(text.strip())
                    self.plans.save()
                    self.touch(target, day)
            canvas.ask('then', then)
        elif name == 'rename':
            def rename(text):
                if text.strip():
                    self.plans.find(day, d['id'])['title'] = text.strip()
                    self.plans.save()
                    self.touch(node, day)
            canvas.ask('what if', rename, text=d['what']['title'])
        elif name == 'drop' and node.kind == 'step':
            self.plans.find(day, d['plan'])['steps'].pop(d['step'])
            self.plans.save()
            self.touch(node.parent, day)
        elif name == 'drop':
            def drop(key):
                if key == 'y':
                    state = self.plans.edit(day)
                    state['plans'] = [p for p in state['plans'] if p['id'] != d['id']]
                    if state['kept'] == d['id']:
                        state['kept'] = None
                    self.plans.save()
                    self.touch(node.parent, day)
                    canvas.say('thrown away')
            canvas.ask(f'throw away "{short(d["what"]["title"])}"?  y yes  n no', drop, choices='yn')
        return None

    def what_if(self, node, canvas):
        d = node.data or {}
        instead = None
        if node.kind == 'event':
            instead = d['event']
        elif node.kind == 'day' and len(d['items']) == 1 and d['items'][0][0] == 'event':
            instead = d['items'][0][1]
        elif node.kind == 'alt' and d['kind'] == 'event':
            instead = d['what']

        def add(day, title):
            state = self.plans.edit(day)
            state['plans'].append({'id': 'w' + uuid.uuid4().hex[:8], 'title': title,
                                   'instead_of': [instead.uid] if instead else [], 'steps': []})
            self.plans.save()
            self.touch(node, day)
            self.pulse(f'{ROOT}/{day:%Y-%m}/{day}')
            canvas.say(f'{dated(day)} forks: weigh it, then ⌃K keep one')

        if node.kind == 'month':
            m = d['month']

            def on(text):
                got = re.fullmatch(r'\s*(\d{1,2})\s+(.+)', text)
                try:
                    day = m.replace(day=int(got[1])) if got else None
                except ValueError:
                    day = None
                if day is None:
                    canvas.say(f'say the day first: "12 {text.strip() or "dinner with D"}"')
                elif got[2].strip():
                    add(day, got[2].strip())
            canvas.ask(f'what if, in {m:%B} (the day, then what)', on)
            return
        day = d['day'] if 'day' in d and d['day'] else (d['event'].first if 'event' in d else None)
        if day is None:
            return
        prompt = f'what if, instead of {short(instead.title)}' if instead else f'what if, on {dated(day)}'
        canvas.ask(prompt, lambda text: add(day, text.strip()) if text.strip() else None)


# -- words

def add_months(d, k):
    y, m = divmod(d.month - 1 + k, 12)
    return d.replace(year=d.year + y, month=m + 1, day=1)


def month_name(m, today):
    return f'{m:%B}' if m.year == today.year else f'{m:%B %Y}'


def dated(d):
    """'24 Sat Oct': the number first, as every day on the canvas."""
    return f'{d.day} {d:%a %b}'


def span_dates(e):
    a, b = e.first, e.last
    return f'{a.day:>2} {a:%a} → {b.day} {b:%a}' if (a.year, a.month) == (b.year, b.month) else \
        f'{a.day:>2} {a:%a %b} → {b.day} {b:%a %b}'


def shade(day):
    """`day`'s colour: its month's, lighter or darker by its week (weeks start on Monday)."""
    week = (day.day - 1 + day.replace(day=1).weekday()) // 7
    base = MONTHS[day.month]
    h, l, s = colorsys.rgb_to_hls(*(int(base[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    r, g, b = colorsys.hls_to_rgb(h, min(0.85, max(0.3, l + SHADES[week])), s)
    return f'#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}'


def when_text(e):
    a = e.start
    if all_day(a):
        if e.span:
            return f'{dated(e.first)} → {dated(e.last)}, {(e.last - e.first).days + 1} days'
        return f'{dated(a)} {a:%Y}, all day'
    b = e.end
    out = f'{dated(a)}  {a:%H:%M}'
    if b > a:
        out += f' → {b:%H:%M}' if b.date() == a.date() else f' → {b.day} {b:%a}  {b:%H:%M}' if (b - a) < 7 * DAY else \
            f' → {dated(b)}  {b:%H:%M}'
    return out


def clean(note):
    paras = [p.strip() for p in re.split(r'\n\s*\n', note or '')]
    text = '\n\n'.join(p for p in paras if p and not any(b in p for b in BOILERPLATE))
    return text if len(text) <= NOTE_MAX else text[:NOTE_MAX].rsplit(' ', 1)[0] + ' …'


def short(text, room=28):
    return fc.clip(text, room)


def alt_title(a):
    return a[2].title if a[1] == 'event' else a[2]['title'] if a[1] == 'plan' else a[2]
