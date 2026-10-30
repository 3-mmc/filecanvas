# twigs

Small trees you move across. Your files, mail, chats, calendar and tasks, each as a tree on one
canvas: a branch opens beside the one you're on, what you've chosen stays lit, and you go by
arrows, typing to filter. Each app is a **twig**. Use one, some, or all of them side by side.

```
git clone https://github.com/3-mmc/twigs && cd twigs
./twigs-launcher install            # or: install msg cal   (just those); puts `twigs` on your PATH
twigs apps                          # which twigs there are, and what each still needs
twigs                               # Emails, Messages, Calendar, TuiDo: each a branch, as they're set up
```

Python 3.11 or newer and nothing else for the canvas itself; each twig says what it needs.

| twig | what | needs | also called |
| --- | --- | --- | --- |
| `files` | folders and files; `cdi` in your shell goes where you choose, ⌃T puts a path at the cursor | — | |
| `msg` | mail (folders, threads, who replied to whom, ⌃Z snooze, send later) and chats (WhatsApp, Signal, Instagram) | isync, notmuch, msmtp, pass, python3-notmuch2; [nchat](https://github.com/3-mmc/nchat) for chats | `msgcanvas` |
| `cal` | calendars from .ics feeds and files, reminders, eras, and what-ifs that fork a day | — | `calcanvas` |
| `tasks` | [tuido](https://github.com/3-mmc/TuiDo)'s desk: paths, library, habits, projects; due days, timers | tuido | `tuido --canvas` |

How each one works: [docs/msg.md](docs/msg.md), [docs/cal.md](docs/cal.md),
[docs/core.md](docs/core.md) (the canvas, `files`, and writing a provider). For cdi:
`eval "$(twigs files --init bash)"` in `~/.bashrc` (zsh and fish likewise).

## What every twig shares

The canvas (`twigs/core.py`) sleeps until something's due and draws only the lines that change,
so these cost nothing while nothing happens:

- **Beacons**: what's new and calls for you (unread mail, a reminder gone off, a task gone late, a
  timer that's rung) is white on red, blinking fast at first and slower as it ages, then lit.
- **Countdowns that stay true**: `in 3h`, `4m ago`, `⏱ 23m` turn over the moment they change.
- **Glow**: what's changed lately (a file you just saved) is tinted, fading over a week.
- **Later**: `2h`, `tonight`, `tomorrow 9`, `mon`, `3 nov` mean the same everywhere (`twigs/kit/when.py`).

`CDECK_CALM=1` keeps it all still (beacons lit, nothing blinking).

## Growing a twig

A twig is a package with a provider (what's in the tree: `children()`, and whichever looks it
wants, listed on `Provider` in `twigs/core.py`) and an `APP` manifest:

```python
APP = dict(name='notes', title='notes', alias=None, summary='your notes, by folder',
           cli='mytwig.cli:main',              # `twigs notes …` runs main(argv)
           hub='mytwig.cli:sections',          # what it adds to `twigs` alone: [(name, Provider)]
           needs=[('rg', 'ripgrep')])          # (a command, or 'module:name', and how to get it)
```

Twigs in this repo live in `twigs/apps/`; one in another package registers under the entry point
group `twigs.apps` (its value the module holding `APP`), and `twigs apps` finds it.

`twigs/core.py` is one file with only the standard library, so it can be copied into a program
that wants the canvas without depending on twigs: [cdeck](https://github.com/vmagierski/cdeck) does,
for its file canvas.

## Tests

```
python3 -m unittest                                              # from here: a private home, never yours
PYTHONPATH=$PWD path/to/tuido/.venv/bin/python -m pytest tests/test_tasks.py   # tasks, with tuido
```
