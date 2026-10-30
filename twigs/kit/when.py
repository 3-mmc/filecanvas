"""Time as people say it, for every twig: a moment ('2h', 'tonight', 'tomorrow 9:00', 'mon',
'3 nov 14:00': parse, say), a day ('fri', 'tomorrow', '3 nov', '+3d', 'next week': day, due_text)
and a length ('25', '25m', '1h30': minutes)."""
import datetime as dt, re
from datetime import date, timedelta

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

DAY_SAYS = 'fri  tomorrow  3 nov  +3d  next week'


def day(text, today):
    """The day `text` names: on or after `today`, or a date this year gone within six months (late);
    None if it names none."""
    t = ' '.join(text.lower().replace(',', ' ').split())
    if t in ('today', 'tod'):
        return today
    if t in ('tomorrow', 'tmrw'):
        return today + timedelta(days=1)
    if t == 'next week':
        return today + timedelta(days=7 - today.weekday())
    m = re.fullmatch(r'\+?(\d+)\s*(d|days?|w|weeks?)', t)
    if m:
        return today + timedelta(days=int(m[1]) * (7 if m[2][0] == 'w' else 1))
    wd = next((i for i, full in enumerate(FULL) if len(t) >= 3 and full.startswith(t)), None)
    if wd is not None:
        return today + timedelta(days=(wd - today.weekday()) % 7)
    m = re.fullmatch(r'(\d{4})-(\d\d)-(\d\d)', t)
    try:
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = re.fullmatch(r'(\d{1,2}) ([a-z]{3})[a-z]*|([a-z]{3})[a-z]* (\d{1,2})', t)
        if not m or (m[2] or m[3]) not in MONTHS:
            return None
        got = date(today.year, MONTHS.index(m[2] or m[3]) + 1, int(m[1] or m[4]))
    except ValueError:
        return None
    # gone this year: next year's, unless it's lately gone (a due day that's late, said again)
    return got if got >= today - timedelta(days=183) else got.replace(year=got.year + 1)


def minutes(text):
    """How many minutes `text` says ('25', '25m', '1h', '1h30', '90 min'); None if it says none."""
    t = text.lower().replace(' ', '')
    m = re.fullmatch(r'(?:(\d+)h)?(?:(\d+)(?:m|min|mins)?)?', t)
    if not t or not m or not (m[1] or m[2]):
        return None
    got = int(m[1] or 0) * 60 + int(m[2] or 0)
    return got or None


def due_text(due, today):
    """'today', 'tomorrow', 'fri' (this week), '3 Nov'; '2 days late' once it's gone."""
    days = (due - today).days
    if days < 0:
        return 'a day late' if days == -1 else f'{-days} days late'
    if days == 0:
        return 'due today'
    if days == 1:
        return 'due tomorrow'
    return f'due {due:%a}' if days < 7 else f'due {due.day} {due:%b}'
