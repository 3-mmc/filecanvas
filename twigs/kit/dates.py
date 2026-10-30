"""Shared date branches for mail and chats."""
import time


def when_group(stamp, now=None):
    """Which group a thread whose newest message came at `stamp` is in, by date."""
    t, now = time.localtime(stamp), time.localtime(now or time.time())
    day = lambda x: time.mktime((x.tm_year, x.tm_mon, x.tm_mday, 0, 0, 0, 0, 0, -1))
    today = day(now)
    since = today - day(t)
    if since <= 0:
        return 'Today'
    if since <= 86400 + 3600:                   # (a summer-time change makes a day 23 or 25 hours)
        return 'Yesterday'
    monday = today - now.tm_wday * 86400
    if stamp >= monday:
        return 'This week'
    if stamp >= monday - 7 * 86400:
        return 'Last week'
    if (t.tm_year, t.tm_mon) == (now.tm_year, now.tm_mon):
        return 'Earlier this month'
    if t.tm_year == now.tm_year:
        return time.strftime('%B', t)
    return str(t.tm_year)

