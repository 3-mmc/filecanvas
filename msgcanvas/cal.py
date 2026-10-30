"""The calendar: months → days → what's on them. Where two things on a day collide, or where you
wonder about a day ("what if…", ⌃W), the day forks into its alternatives: you weigh them side by
side, each with what would follow it (⌃N then…), and keep one (⌃K); the rest fade, kept to come
back to.

Calendars are folders of .ics files (vdirsyncer's layout, or Google Calendar's export unzipped),
a folder or a file a calendar, under ~/.local/share/msgcanvas/calendars/. What-ifs are kept here
only (state: whatif.json): nothing is written to a calendar.

Repeating events: FREQ with INTERVAL, COUNT, UNTIL, EXDATE and moved occurrences; BYDAY and the
other BY… parts aren't read yet (an event repeats on its first day's weekday, date)."""
import datetime as dt, json, os, re, threading, time, uuid, zlib
from zoneinfo import ZoneInfo

from . import accounts, treecanvas as fc

CALENDARS = os.path.join(accounts.DATA, 'calendars')
NOTE_WIDTH = 60
NOTE_MAX = 700          # characters of a description shown; a long invitation's start
ROOT = '@cal'
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
    """A DTSTART-like value: a date (all day), or a datetime in `zone`."""
    if params.get('VALUE') == 'DATE' or re.fullmatch(r'\d{8}', value):
        return dt.date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    t = dt.datetime.strptime(value[:15], '%Y%m%dT%H%M%S')
    if value.endswith('Z'):
        return t.replace(tzinfo=dt.timezone.utc).astimezone(zone)
    try:
        return t.replace(tzinfo=ZoneInfo(params['TZID'].strip('"'))).astimezone(zone)
    except Exception:           # no TZID, or one only Windows knows (Outlook's): taken as here
        return t.replace(tzinfo=zone)


def all_day(t):
    return type(t) is dt.date


def day_of(t):
    return t if all_day(t) else t.date()


class Event:
    """One occurrence: `uid` is unique among them (a repeat's has its day), `start` and `end` dates
    (all day, end not included) or datetimes here."""
    def __init__(self, uid, title, start, end=None, where='', note='', calendar=''):
        self.uid, self.title, self.where, self.note, self.calendar = uid, title, where, note, calendar
        self.start = start
        self.end = end if end is not None and end >= start else (start + DAY if all_day(start) else start)

    @property
    def first(self):
        return day_of(self.start)

    @property
    def last(self):
        if all_day(self.start):
            return max(self.first, self.end - DAY)
        if self.end - self.start < DAY:        # a night out ends tomorrow, but it's tonight's
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
    """The VEVENTs in an .ics text, each {NAME: [(params, value)]}."""
    out, cur = [], None
    for line in re.sub(r'\r?\n[ \t]', '', text).splitlines():
        head, _, value = line.partition(':')
        name, *params = head.split(';')
        name = name.upper()
        if name == 'BEGIN' and value.upper() == 'VEVENT':
            cur = {}
        elif name == 'END' and value.upper() == 'VEVENT':
            if cur is not None:
                out.append(cur)
            cur = None
        elif cur is not None:
            cur.setdefault(name, []).append((dict(p.split('=', 1) for p in params if '=' in p), value))
    return out


def shifted(t, freq, k):
    """`t` moved on k steps of `freq`; None where that day doesn't exist (31 April)."""
    try:
        if freq == 'DAILY':
            return t + k * DAY
        if freq == 'WEEKLY':
            return t + k * 7 * DAY
        if freq == 'MONTHLY':
            y, m = divmod(t.month - 1 + k, 12)
            return t.replace(year=t.year + y, month=m + 1)
        if freq == 'YEARLY':
            return t.replace(year=t.year + k)
    except ValueError:
        return None
    return None


def repeats(start, rule, zone, horizon):
    r = dict(p.split('=', 1) for p in rule.split(';') if '=' in p)
    freq, step = r.get('FREQ'), max(1, int(r.get('INTERVAL', 1)))
    if freq not in ('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'):    # hourly and the like: just the first
        yield start
        return
    count = int(r['COUNT']) if 'COUNT' in r else None
    until = day_of(when(r['UNTIL'], {}, zone)) if 'UNTIL' in r else None
    n = 0
    for i in range(5000):
        t = shifted(start, freq, i * step)
        if t is None:           # 31 April, 29 February: skipped, as calendars do
            continue
        if (until and day_of(t) > until) or day_of(t) > horizon or (count is not None and n >= count):
            break
        n += 1
        yield t


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
        if 'DTSTART' not in c or get('STATUS').upper() == 'CANCELLED':
            continue
        p, v = c['DTSTART'][0]
        start = when(v, p, zone)
        if 'DTEND' in c:
            p, v = c['DTEND'][0]
            end = when(v, p, zone)
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
                    out.append(Event(f'{uid}@{day_of(t)}', title, t, end and t + (end - start), where, note, calendar))
        else:
            rid = ''
            if 'RECURRENCE-ID' in c:
                p, v = c['RECURRENCE-ID'][0]
                rid = f'@{day_of(when(v, p, zone))}'
            out.append(Event(uid + rid, title, start, end, where, note, calendar))
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


def clashes(events):
    """The timed events among `events` that overlap another."""
    timed = [e for e in events if not all_day(e.start)]
    return {e.uid for e in timed for f in timed if e is not f and e.start < f.end and f.start < e.end}


# -- the provider

class Calendar(fc.Provider):
    """Months → days → what's on them, and the forks of what-ifs. `KINDS` are the node kinds it
    answers for."""
    KINDS = ('calendar', 'month', 'day', 'span', 'event', 'fork', 'alt', 'step', 'detail')

    def __init__(self, folder=CALENDARS, plans=None, today=None, watch=2.0, zone=None):
        self.folder, self.zone = folder, zone or local_zone()
        self.fixed_today = today
        self.plans = Plans(plans)
        self.lock = threading.Lock()
        self.load()
        if watch:
            threading.Thread(target=self.watch, args=(watch,), daemon=True).start()

    def today(self):
        return self.fixed_today or dt.datetime.now(self.zone).date()

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
        return sig, self.today()

    def watch(self, every):
        seen = self.signature()
        while True:
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
            if e.span:
                spans.append(e)
            else:
                by_day.setdefault(e.first, []).append(e)
        for es in by_day.values():
            es.sort(key=lambda e: (not all_day(e.start), e.start if not all_day(e.start) else dt.datetime.min.replace(tzinfo=self.zone), e.title))
        with self.lock:
            self.names, self.by_day, self.spans = names, by_day, sorted(spans, key=lambda e: (e.first, e.title))

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
        """The month's rows: ('span', Event) for what runs over days, ('day', date, items)."""
        end = add_months(m, 1)
        rows = [((max(s.first, m), 0), ('span', s)) for s in self.spans if s.first < end and s.last >= m]
        days = {d for d in self.by_day if m <= d < end} | \
            {d for d in map(dt.date.fromisoformat, self.plans.days) if m <= d < end}
        today = self.today()
        if m <= today < end:
            days.add(today)
        for d in days:
            rows.append(((d, 1), ('day', d, self.items(d))))
        return [r for _, r in sorted(rows, key=lambda r: r[0])]

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
                out = []
                for row in self.month_rows(d['month']):
                    if row[0] == 'span':
                        e = row[1]
                        out.append(fc.Node(f'{node.path}/s:{e.uid}', f'{span_dates(e)}  {e.title}', True, node,
                                           'span', data={'event': e}))
                    else:
                        _, day, items = row
                        out.append(fc.Node(f'{node.path}/{day}', self.day_label(day, items), bool(items), node,
                                           'day', data={'day': day, 'items': items}))
                return out
            if k == 'day':
                items = d['items']
                if len(items) == 1:     # a day with one thing on it is that thing
                    kind, what = items[0]
                    return self.alternatives(node, d['day'], what) if kind == 'fork' else self.details(node, what, d['day'])
                return [self.item(node, d['day'], kind, what) for kind, what in items]
            if k == 'fork':
                return self.alternatives(node, d['day'], d['alts'])
            if k in ('span', 'event'):
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
        return fc.Node(f'{parent.path}/{what.uid}', what.title, True, parent, 'event', data={'event': what, 'day': day})

    def alternatives(self, parent, day, alts):
        out = []
        for aid, kind, what in alts:
            if kind == 'event':
                name = what.title
            elif kind == 'plan':
                name = what['title']
            else:
                name = what
            out.append(fc.Node(f'{parent.path}/{aid}', name, kind == 'event' or (kind == 'plan' and bool(what['steps'])),
                               parent, 'alt', data={'day': day, 'id': aid, 'kind': kind, 'what': what, 'alts': alts}))
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
            return next(alt_title(a) for a in alts if a[0] == k)
        room = max(10, 34 // len(alts))      # each its share, so the last isn't lost
        return ' or '.join(short(alt_title(a), room) for a in alts)

    def day_label(self, day, items):
        head = f'{day:%a} {day.day:>2}'
        if not items:
            return head + ('  today' if day == self.today() else '')
        kind, what = items[0]
        first = self.fork_label(day, what) if kind == 'fork' else what.title
        return f'{head}  {first}' + (f'  +{len(items) - 1}' if len(items) > 1 else '')

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
            n = len(self.month_rows(d['month']))
            n -= d['month'] <= self.today() < add_months(d['month'], 1) and not self.items(self.today())
            return (str(n), 'stub', False) if n else None
        if node.kind == 'span':
            return f'{(d["event"].last - d["event"].first).days + 1} days', 'stub', False
        if node.kind == 'day' and len(d['items']) == 1 and d['items'][0][0] == 'event':
            at = d['items'][0][1].at()
            return (at, 'stub', False) if at else None
        if node.kind == 'event' or (node.kind == 'alt' and d['kind'] == 'event'):
            e = d['event'] if node.kind == 'event' else d['what']
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
        if node.kind == 'day' and d['day'] < self.today():
            return 0.7
        return 1.0

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

    def touch(self, node, *also):
        """What `node` shows has changed: it and every branch above it, up to its month, again."""
        while node is not None and node.kind != 'calendar':
            self.invalidate(node.path)
            node = node.parent
        for path in also:
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
            self.touch(node.parent)
            self.pulse(node.parent.path)
        elif name == 'then':
            pid = d['what']['id'] if node.kind == 'alt' else d['plan']
            target = node if node.kind == 'alt' else node.parent

            def then(text):
                if text.strip():
                    self.plans.find(day, pid)['steps'].append(text.strip())
                    self.plans.save()
                    self.touch(target)
            canvas.ask('then', then)
        elif name == 'rename':
            def rename(text):
                if text.strip():
                    self.plans.find(day, d['id'])['title'] = text.strip()
                    self.plans.save()
                    self.touch(node)
            canvas.ask('what if', rename, text=d['what']['title'])
        elif name == 'drop' and node.kind == 'step':
            self.plans.find(day, d['plan'])['steps'].pop(d['step'])
            self.plans.save()
            self.touch(node.parent)
        elif name == 'drop':
            def drop(key):
                if key == 'y':
                    state = self.plans.edit(day)
                    state['plans'] = [p for p in state['plans'] if p['id'] != d['id']]
                    if state['kept'] == d['id']:
                        state['kept'] = None
                    self.plans.save()
                    self.touch(node.parent)
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
            month = f'{ROOT}/{day:%Y-%m}'
            self.touch(node, month, f'{month}/{day}')
            self.pulse(f'{month}/{day}')
            canvas.say(f'{day:%a %-d %b} forks: weigh it, then ⌃K keep one')

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
        prompt = f'what if, instead of {short(instead.title)}' if instead else f'what if, on {day:%a %-d %b}'
        canvas.ask(prompt, lambda text: add(day, text.strip()) if text.strip() else None)


# -- words

def add_months(d, k):
    y, m = divmod(d.month - 1 + k, 12)
    return d.replace(year=d.year + y, month=m + 1, day=1)


def month_name(m, today):
    return f'{m:%B}' if m.year == today.year else f'{m:%B %Y}'


def span_dates(e):
    a, b = e.first, e.last
    return f'{a:%a} {a.day}–{b:%a} {b.day}' if (a.year, a.month) == (b.year, b.month) else \
        f'{a:%b} {a.day}–{b:%b} {b.day}'


def when_text(e):
    a = e.start
    if all_day(a):
        if e.span:
            return f'{e.first:%a %-d %b} → {e.last:%a %-d %b}, {(e.last - e.first).days + 1} days'
        return f'{a:%a %-d %b %Y}, all day'
    b = e.end
    out = f'{a:%a %-d %b}  {a:%H:%M}'
    if b > a:
        out += f' → {b:%H:%M}' if b.date() == a.date() else f' → {b:%a %H:%M}' if (b - a) < 7 * DAY else \
            f' → {b:%a %-d %b %H:%M}'
    return out


def clean(note):
    paras = [p.strip() for p in re.split(r'\n\s*\n', note or '')]
    text = '\n\n'.join(p for p in paras if p and not any(b in p for b in BOILERPLATE))
    return text if len(text) <= NOTE_MAX else text[:NOTE_MAX].rsplit(' ', 1)[0] + ' …'


def short(text, room=28):
    return fc.clip(text, room)


def alt_title(a):
    return a[2].title if a[1] == 'event' else a[2]['title'] if a[1] == 'plan' else a[2]
