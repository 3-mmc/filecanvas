# calcanvas

Your calendar on a canvas you move across, and what-ifs that fork its days. Drawn by
[treecanvas](https://github.com/3-mmc/treecanvas) through its providers (vendored here,
`tools/vendor-treecanvas.py`); [msgcanvas](https://github.com/3-mmc/msgcanvas) shows the same
calendar beside your mail and chats.

```
ln -s "$PWD/calcanvas-launcher" ~/.local/bin/calcanvas    # once: `calcanvas` anywhere
calcanvas add NAME           # subscribe to a calendar's .ics address (asked for, unseen)
calcanvas [YYYY-MM]          # open it, on this month (or that one)
calcanvas list | sync [NAME] | remove NAME
```

## The canvas

It opens on this month, at today. Months branch into what's on them, each day as `30 Fri  …`:
its number and weekday in its month's colour (October orange, December teal…), each week a shade
of it. A day with one thing is that thing (its time in a column of their own), a day with more
opens into them. Each thing opens onto when, where, and what it says. Within two days, a thing's
time moves to the front and the column says how long until it (`in 3h`, `tomorrow`, `now`).

What runs over days (a trip, a visit) is a bar in its own colour among the days it starts on, and
an era: it opens onto what happens within it, eras inside it too, and while you're in one it says
how far (`day 7 of 8`). A night out, even a 24-hour one, stays the night it starts.

## What-ifs

Where two things on a day overlap, the day forks (⑂) into them, and the bar counts what's still
to decide. **⌃W** asks "what if…" anywhere: on a day it weighs your idea against the day as it
is; on something on the calendar, against that; on a month, say the day first (`12 dinner with
D`). Each alternative can grow what would follow it (**⌃N** then…). **⌃K** keeps one: it's ticked
and its branch lit, the rest fade (to dust in the bar) but stay, to change your mind (⌃K again).
**⌃E** renames a what-if, **⌃D** throws it away. What-ifs are kept in
`~/.local/state/calcanvas/whatif.json`, shared with msgcanvas: nothing is written to a calendar.

## Calendars

**Feeds** (`calcanvas add`): a calendar's .ics address, fetched every 15 minutes while the canvas
is open (a chip in the bar while it does), and by `calcanvas sync`. Asking first whether it's
changed costs little. Read-only. A public calendar (holidays, the moon) is **quiet**: its days come
after your own things, never lead a day's label, show fainter, don't count down or count in a
month, and are fetched twice a day (`quiet = false` / `every = 15` in feeds.toml to change it).

| where | the address |
| --- | --- |
| Google Calendar | Settings → the calendar → Integrate calendar → **Secret address in iCal format** (yours); **Public address in iCal format** (holidays and other public ones) |
| Outlook / Microsoft 365 | Settings → Calendar → Shared calendars → Publish a calendar → ICS |
| anything else | its .ics or `webcal://` link |

They're listed in `~/.config/calcanvas/feeds.toml`, readable only by you: a secret address is as
good as a password, so it's never printed (errors name only the server).

**Files**: `.ics` files in `~/.local/share/calcanvas/calendars/`, a folder of them per calendar
(vdirsyncer's layout; a `displayname` file names it) or one file per calendar (Google Calendar's
Settings → Export, unzipped). Feeds are fetched into folders there too. Everything is read again
whenever it changes.

Repeats are laid out in the event's own time zone (FREQ, INTERVAL, COUNT, UNTIL, BYDAY with `2TU`
and `-1FR`, BYMONTHDAY, BYMONTH, RDATE, EXDATE, moved occurrences).

## Tests

    python3 -m unittest            # from the repo root: a private home, never yours
