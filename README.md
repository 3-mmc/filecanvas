# filecanvas

A prototype file picker for the terminal: the file system as one canvas you move across.

Every folder you open keeps its place on a tree that branches right, each folder's children
centred on its row and spreading up and down only as far as they need. The camera eases after
your selection and keeps it near the middle; everything you've opened stays on screen, dimmed by
its distance from the path you're on.

It looks like your prompt: the chosen path is drawn along the bottom the way
[powerline-shell](https://github.com/b-ryan/powerline-shell) draws one (`~` in its own chip, the
git branch at the end, pink when the repo has changes), entries take their colours from
`LS_COLORS` as `ls` does, and changed files are marked: `●` changed, `✚` staged, `?` untracked,
`✖` conflicted, `•` on a folder with changes inside. Files git ignores are dimmed.

```
python3 filecanvas.py [START]        # prints the chosen path; nothing on Esc
cd "$(python3 filecanvas.py)"
```

Python 3, no dependencies. Linux, macOS, WSL.

## Keys

| key | does |
| --- | --- |
| ↑ ↓ | move between siblings |
| → or Tab | open a folder |
| ← | go to the parent (the branch stays open) |
| type | filter the current column; Backspace removes a letter, ⌃U clears |
| ⌃X | fold every branch off the current path |
| Enter | choose |
| Esc | cancel |

## Settings

| flag | default | |
| --- | --- | --- |
| `--theme NAME` | powerline | `powerline` (powerline-shell's default), `amber` (cdeck's), `nord`, `gruvbox`, `solarized-dark`, or a `.json` file |
| `--glyphs` | powerline | `powerline` needs a powerline font, `round` a Nerd Font, `plain` nothing |
| `--color` | auto | `truecolor` or `256`; auto uses 256 inside tmux unless `COLORTERM=truecolor` |
| `--[no-]ls-colors` | theme | colour entries from `LS_COLORS`; `amber` turns it off by default, the rest on |
| `--[no-]git` | on | mark changed entries and show the branch |
| `--dead F` | 0.3 | camera dead zone as a fraction of the view; `0` keeps the selection centred |
| `--across F` | 0.4 | where the selection sits across the view, 0..1 |
| `--ease F` | 0.3 | camera easing per frame; `1` jumps (so does `CDECK_CALM=1`) |
| `--fade N` | 2 | rows per grey step in the column you're moving in |
| `--colmax N` | 24 | widest a column gets |
| `--gap N` | 5 | columns between a name and its children |
| `--limit N` | 0 | most children per folder, the rest as "↓ N more"; `0` shows all, trailing off screen |
| `--[no-]peek` | off | show the selected folder's contents before opening it |
| `--[no-]fold` | off | close a branch when you move off it |
| `--[no-]grey-rest` | on | only the branch you're on keeps its colours; branches you're not on go grey |
| `--backdrop` | auto | inside tmux, the screen you came from, dimmed, fills the empty space; `off` leaves it blank |
| `--tab cycle` | branch | Tab moves down instead of opening |
| `--dirs-only` | off | list folders only (for choosing one) |
| `--on-start` | off | begin on the start folder itself, not inside it |

Any of these can live in `~/.config/filecanvas/config.json`, flags still winning, with your own
colours on top of the theme's under `colors`:

```json
{"theme": "nord", "dead": 0, "glyphs": "round", "colors": {"sel_bg": "#ff8800"}}
```

A theme file is the same roles, starting from another theme: `{"base": "gruvbox", "path": 214}`.
The roles are listed at the top of `filecanvas.py` (`THEMES`). Themes only choose colours and
defaults; every setting works with every theme.

## In your shell

`cdi [FOLDER]` goes to a folder you choose on the canvas. It opens on the folder you're in (or
FOLDER), what's in it beside; Enter goes to the folder you're on, Esc stays put. ⌃T puts a chosen
path in at the cursor, quoted. Add one line to your shell's rc file:

```bash
eval "$(python3 /path/to/filecanvas.py --init bash)"      # ~/.bashrc
eval "$(python3 /path/to/filecanvas.py --init zsh)"       # ~/.zshrc
python3 /path/to/filecanvas.py --init fish | source       # ~/.config/fish/config.fish
```

`--cmd NAME` names the command something else, and `--key alt-f` (any `ctrl-` or `alt-` letter,
or `none`) moves the key. If the name is already a command where you are (zoxide's
`--cmd cd` makes a `cdi` of its own), it's left alone and the shell says so at startup.

For your own scripts, `--print absolute` prints the chosen path absolute, and `--print shell`
(or `fish`) quoted for that shell; it prints nothing and exits 1 on Esc.

Started as a prototype for [cdeck](https://github.com/vmagierski/cdeck)'s folder trail.

## As a library

One file, no dependencies, so it can be copied into another project:

```python
import filecanvas
path = filecanvas.pick('~/src/cd', dirs_only=True, theme='amber')   # absolute path, or None
```

A start that doesn't exist yet (`~/src/cd`) opens its folder filtered by the rest. Any flag can
be passed by name; `theme` may also be a dict (`{"base": "nord", "sel_bg": 31}`), `header` / `footer`
functions of the width returning a line for the top / bottom, `on_start=True` begins on the start
folder itself (for choosing a folder), `defaults` sets defaults that config.json can still change, and `skip` names config.json keys to ignore
(a host app keeping its own look passes `skip=('theme', 'colors')`).
