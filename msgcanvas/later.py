"""Later: when you say ('2h', 'tonight', 'tomorrow 9:00', 'mon', '3 nov 14:00'), and what's
waiting for then: threads snoozed till then, drafts to be sent then. Kept in a JSON file in
msgcanvas's state; nothing is written to notmuch or the server."""
import datetime as dt, json, os, re

MORNING, EVENING = dt.time(8, 0), dt.time(19, 0)
MONTHS = ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec')
SAYS = "2h  30m  tonight  tomorrow  mon 9:00  3 nov"     # what a prompt offers


FULL = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


def weekday(word):
    """0-6 for 'mon', 'Monday', 'tues'…; None for anything else."""
    return next((i for i, full in enumerate(FULL) if len(word) >= 3 and full.startswith(word)), None)


def parse(text, now=None):
    """When `text` means, after `now` (an aware datetime, here); None if it means nothing, or a
    time gone. A day without a time is its morning (8:00), 'tonight' its evening (19:00)."""
    now = now or dt.datetime.now().astimezone()
    words = [w for w in text.lower().replace(',', ' ').split() if w not in ('at', 'on', 'in')]
    if not words:
        return None
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(m|mins?|minutes?|h|hrs?|hours?|d|days?|w|weeks?)', ''.join(words))
    if m:
        return now + dt.timedelta(minutes=float(m[1])) * {'m': 1, 'h': 60, 'd': 1440, 'w': 10080}[m[2][0]]
    clock = None
    m = re.fullmatch(r'(\d{1,2})(?::(\d\d))?(am|pm)?', words[-1])
    days = ('today', 'tonight', 'tomorrow', 'tmrw')
    if m and (m[2] or m[3] or len(words) == 1 or words[-2] in days or weekday(words[-2]) is not None):
        h, mi = int(m[1]), int(m[2] or 0)
        if h > (12 if m[3] else 23) or mi > 59 or (m[3] and h == 0):
            return None
        if m[3]:
            h = h % 12 + (12 if m[3] == 'pm' else 0)
        clock, words = dt.time(h, mi), words[:-1]
    t = ' '.join(words)
    if t in ('', 'today'):
        day = now.date()
    elif t == 'tonight':
        day, clock = now.date(), clock or EVENING
    elif t in ('tomorrow', 'tmrw'):
        day = now.date() + dt.timedelta(days=1)
    elif t == 'next week':
        day = now.date() + dt.timedelta(days=7 - now.weekday())
    elif t in ('weekend', 'the weekend', 'this weekend'):
        day = now.date() + dt.timedelta(days=(5 - now.weekday()) % 7 or 7)
    elif len(words) == 1 and weekday(t) is not None:
        ahead = (weekday(t) - now.weekday()) % 7
        day = now.date() + dt.timedelta(days=ahead or (0 if clock and clock > now.time() else 7))
    else:
        m = re.fullmatch(r'(\d{1,2}) ([a-z]{3})[a-z]*(?: (\d{4}))?|([a-z]{3})[a-z]* (\d{1,2})(?: (\d{4}))?', t)
        if not m:
            return None
        d, mon, y = (m[1], m[2], m[3]) if m[1] else (m[5], m[4], m[6])
        if mon not in MONTHS:
            return None
        try:
            day = dt.date(int(y) if y else now.year, MONTHS.index(mon) + 1, int(d))
        except ValueError:
            return None
        if not y and day < now.date():
            day = day.replace(year=day.year + 1)
    when = dt.datetime.combine(day, clock or MORNING, now.tzinfo)
    if when <= now and t in ('', 'today') and clock is not None:     # '9:00' when it's past: tomorrow's
        when += dt.timedelta(days=1)
    return when if when > now else None


def say(when, now=None):
    """'today 19:00', 'tomorrow 8:00', '2 Mon Nov 8:00': the number first, as the calendar has it."""
    now = now or dt.datetime.now().astimezone()
    days = (when.date() - now.date()).days
    day = 'today' if days == 0 else 'tomorrow' if days == 1 else f'{when.day} {when:%a %b}'
    return f'{day} {when.hour}:{when:%M}'


class Later:
    """{'snoozed': {thread id: [until, account key, folder]}, 'woke': {thread id: [when it came
    back, account key, folder]}, 'sending': {draft path: [when, account key]}}, in a JSON file
    (None: kept in memory)."""
    def __init__(self, path):
        self.path = path
        try:
            with open(path) as f:
                got = json.load(f)
        except (TypeError, OSError, ValueError):
            got = {}
        self.snoozed, self.woke, self.sending = (got.get(k, {}) for k in ('snoozed', 'woke', 'sending'))

    def save(self):
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path + '.new', 'w') as f:
            json.dump({'snoozed': self.snoozed, 'woke': self.woke, 'sending': self.sending}, f, indent=1)
        os.replace(self.path + '.new', self.path)

    def due(self, now):
        """(threads whose snooze is over, drafts whose time has come), by `now` (epoch seconds)."""
        return ([t for t, v in self.snoozed.items() if v[0] <= now],
                [p for p, v in self.sending.items() if v[0] <= now])

    def next(self):
        """The soonest anything here is due (epoch seconds), or None."""
        got = [v[0] for v in self.snoozed.values()] + [v[0] for v in self.sending.values()]
        return min(got) if got else None
