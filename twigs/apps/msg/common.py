"""Shared message identifiers, and how mail is shown; independent of any mail or chat backend."""
import email.utils, time

SEP = "\x1f"

BODY_WIDTH = 66       # columns a message's body wraps to on the canvas
BODY_LINES = 40       # lines of a body shown at first; ↓ more shows MORE_LINES more
MORE_LINES = 80

# Folders shown first, in this order, by the start of their name in English, German or Czech.
ORDER = (('INBOX',), ('Sent', 'Gesendet', 'Odeslan'), ('Drafts', 'Entwürfe', 'Koncept'),
         ('Archive', 'Archiv'), ('Starred', 'Markiert', 'S hvězdičkou'),
         ('Junk', 'Spam', 'Nevyžádan'), ('Trash', 'Deleted', 'Gelöscht', 'Papierkorb', 'Koš'))


def when(stamp, now=None):
    """A short date: the time today, the day this year, the day and year before."""
    t, now = time.localtime(stamp), time.localtime(now or time.time())
    if t[:3] == now[:3]:
        return time.strftime('%H:%M', t)
    day = f'{t.tm_mday} {time.strftime("%b", t)}'
    return day if t.tm_year == now.tm_year else f'{day} {t.tm_year}'


def who(header):
    """The name in a From line, else the address's mailbox."""
    name, addr = email.utils.parseaddr(header or '')
    return name or addr.split('@')[0] or '(nobody)'


def first_author(authors):
    """Who began a thread, from notmuch's authors ('Ann, Bo| Cy': matched ones before the |)."""
    return (authors or '').replace('|', ',').split(',')[0].strip() or '(nobody)'


def folder_name(rel):
    """How a folder is shown: Inbox, and Gmail's [Gmail]/ dropped."""
    if rel == 'INBOX':
        return 'Inbox'
    return rel[len('[Gmail]/'):] if rel.startswith('[Gmail]/') else rel


def folder_rank(rel):
    name = folder_name(rel)
    hit = next((i for i, names in enumerate(ORDER)
                if any(name.casefold().startswith(o.casefold()) for o in names)), len(ORDER))
    return hit, name.casefold()
