"""calcanvas: your calendar on a canvas you move across, and what-ifs that fork its days.

    calcanvas [YYYY-MM]          open it on this month (or that one; Esc leaves)
    calcanvas add NAME [URL]     subscribe to a calendar's .ics address (asked for, unseen, if not given)
    calcanvas remove NAME        unsubscribe, and forget what was fetched
    calcanvas list               what you're subscribed to, and when each last came
    calcanvas sync [NAME]        fetch now
"""
import getpass, os, shutil, sys, time

from . import cal, feeds, treecanvas as fc

LOOK = dict(colmax=40, glyphs='plain', ease=1 if os.environ.get('CDECK_CALM') else 0.3, peek=True)
WHERE = ('Google Calendar: Settings → (the calendar, on the left) → Integrate calendar → '
         '"Secret address in iCal format".\nOutlook: Settings → Calendar → Shared calendars → Publish a calendar → ICS.')


def calendar():
    from . import notify
    return cal.Calendar(plans=cal.WHATIF, feeds=feeds.load(), fetch=feeds.fetch, notify=notify.notify,
                        reminded=cal.REMINDED)


def find(name):
    got = feeds.load()
    hit = [f for f in got if name in (f.name, f.key)]
    if not hit:
        sys.exit(f"calcanvas: no feed {name!r} (feeds: {', '.join(f.name for f in got) or 'none'})")
    return got, hit[0]


def add(name, url=None):
    got = feeds.load()
    if any(name in (f.name, f.key) for f in got):
        sys.exit(f'calcanvas: there is already a feed {name!r}')
    if not url:
        print(f'The .ics address for {name}. {WHERE}\n(It isn\'t shown as you paste it: it\'s secret.)')
        url = getpass.getpass('address: ').strip()
    if not url.startswith(('https://', 'http://', 'webcal://')):
        sys.exit('calcanvas: that isn\'t a web address (https://… or webcal://…)')
    feed = feeds.Feed(name, url, quiet=feeds.public(url))      # holidays and such: in the background
    if feed.quiet:
        feed.every = 720                                            # and they hardly change
    while any(f.key == feed.key for f in got):
        feed.key += '-2'
    try:
        feeds.fetch(feed, force=True)
    except OSError as e:
        sys.exit(f'calcanvas: {e}')
    feeds.save(got + [feed])
    print(f'{name}: {feeds.count(feed)} events from {feed.where()}, fetched every {feed.every} minutes '
          'while calcanvas is open (`calcanvas sync` any time)'
          + ('; a public calendar, so quiet: after your own things, fainter (quiet = false in feeds.toml to change it).'
             if feed.quiet else '.'))


def remove(name):
    got, feed = find(name)
    feeds.save([f for f in got if f is not feed])
    shutil.rmtree(feeds.folder(feed), ignore_errors=True)
    print(f'{feed.name}: unsubscribed')


def listing():
    got, seen = feeds.load(), feeds.seen()
    if not got:
        print(f'No feeds yet: `calcanvas add NAME`.\n{WHERE}')
    for f in got:
        t = seen.get(f.key, {}).get('checked')
        when = time.strftime('%-d %b %H:%M', time.localtime(t)) if t else 'never'
        print(f'  {f.name:<28} {f.where():<26} {feeds.count(f):>5} events   checked {when}')
    others = [n for n, _ in cal.calendars(cal.CALENDARS) if n not in {f.name for f in got}]
    if others:
        print(f'and from files in {cal.CALENDARS}: {", ".join(others)}')


def sync(name=None):
    got = [find(name)[1]] if name else feeds.load()
    failed = 0
    for f in got:
        try:
            print(f'{f.name}: {"changed" if feeds.fetch(f) == "new" else "no change"} ({feeds.count(f)} events)')
        except OSError as e:
            failed = 1
            print(f'{f.name}: {e}', file=sys.stderr)
    sys.exit(failed)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ''
    if cmd in ('-h', '--help', 'help'):
        print(__doc__.strip())
    elif cmd == 'add' and len(argv) in (2, 3):
        add(*argv[1:])
    elif cmd == 'remove' and len(argv) == 2:
        remove(argv[1])
    elif cmd == 'list' and len(argv) == 1:
        listing()
    elif cmd == 'sync' and len(argv) <= 2:
        sync(*argv[1:])
    elif len(argv) <= 1 and cmd not in ('add', 'remove', 'sync'):
        if not cal.has_calendars() and not feeds.load():
            sys.exit(f'calcanvas: no calendars yet: `calcanvas add NAME` (an .ics address), '
                     f'or .ics files in {cal.CALENDARS}')
        fc.pick(cmd, provider=calendar(), **LOOK)
    else:
        sys.exit(__doc__.strip())


if __name__ == '__main__':
    main()
