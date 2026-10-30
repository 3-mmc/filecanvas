# treecanvas

A tree as one canvas you move across, in the terminal: by default the file system, as a file
picker; given a provider, anything shaped like a tree (mail and chats, in
[msgcanvas](https://github.com/3-mmc/msgcanvas)). It was called filecanvas.

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
python3 treecanvas.py [START]        # prints the chosen path; nothing on Esc
cd "$(python3 treecanvas.py)"
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
| PgUp PgDn | scroll the reading pane (`--pane`) |
| Enter | choose |
| Esc | cancel |

## Settings

| flag | default | |
| --- | --- | --- |
| `--theme NAME` | powerline | `powerline` (powerline-shell's default), `amber` (cdeck's), `nord`, `gruvbox`, `solarized-dark`, or a `.json` file |
| `--glyphs` | powerline | `powerline` needs a powerline font, `round` a Nerd Font, `plain` nothing |
| `--color` | auto | `truecolor` or `256`; auto uses 256 inside tmux unless `COLORTERM=truecolor` |
| `--[no-]ls-colors` | on | colour entries as `ls` does, from `LS_COLORS` (or `ls`'s own when it isn't set) |
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
| `--[no-]pane` | off | a reading pane beside the tree: the start of the chosen file |
| `--pane-width F` | 0.45 | the pane's share of the width |
| `--tree FILE` | | show a JSON file as the tree instead (objects are branches, the rest leaves shown in the pane) |

Any of these can live in `~/.config/treecanvas/config.json`, flags still winning, with your own
colours on top of the theme's under `colors`:

```json
{"theme": "nord", "dead": 0, "glyphs": "round", "colors": {"sel_bg": "#ff8800"}}
```

A theme file is the same roles, starting from another theme: `{"base": "gruvbox", "path": 214}`.
The roles are listed at the top of `treecanvas.py` (`THEMES`). Themes only choose colours and
defaults; every setting works with every theme.

## Other trees

The canvas draws whatever a **provider** gives it; the file system is just the one it starts
with. A provider says what a node's children are, and optionally its colour, a mark beside it,
how bright it is, and the bar along the bottom; the canvas does the layout, the camera, the filter
and the keys. A node's `path` is its id, and Enter returns it.

```python
import treecanvas as fc

class Mail(fc.Provider):
    def children(self, node):                      # the only one you need
        return [fc.Node(f'{node.path}/{t.id}', t.subject, t.replies > 0, node, data=t)
                for t in threads_under(node)]
    def mark(self, node):                          # (theme role, glyph, glyph with no special font)
        return ('git_dirty', '●', '*') if node.data and node.data.unread else None

fc.pick(provider=Mail())
```

`fc.Tree({...})` turns nested dicts into a tree (`--tree FILE.json` from the command line), for
trying it out. The hooks are listed on `Provider` in `treecanvas.py`: `locate`, `label`, `colour`,
`mark`, `dim`, `bar`, and `changed` / `busy` for work that lands in the background.

A provider can also keep the canvas open, the way a mail or chat client would:

- `choose(node)` is what Enter does: a value ends the canvas (`pick` returns it), `None` stays
  (and on a branch, opens it). By default it ends with the node's id.
- `preview(node)` fills a **reading pane** beside the tree (`pane = True`, or `--pane`); it's
  wrapped to fit, and PgUp / PgDn scroll it.
- `invalidate(path)`, safe from any thread, fetches a node's children again before the next frame
  (every node's, with no path). What's open and chosen stays where the ids still are; a chosen
  node that's gone gives way to its neighbour.

The canvas draws only when something's due, and then only the lines that changed. Still, it
sleeps: no frames, no polling. It wakes for a key, a resize, or a provider's news (`invalidate`,
`pulse` and `status` wake it at once, from any thread), and for what it knows will change on its
own:

- `beacon(node)` returns when what a node marks came (`time.time()`): it's drawn white on red,
  blinking fast at first and slower as it ages (each half-blink a little longer, from 0.25 s to
  3 s over an hour), then steadily lit. The canvas sleeps between flips. Calm
  (`CDECK_CALM`) keeps it lit without blinking.
- `next_change()` returns when what's drawn next changes by itself (a countdown ticking over, a
  clock): the canvas draws again then.
- `tint(node)` colours the start of a label in a provider's own colours (a date, a time).

`python3 -m unittest test_treecanvas` runs the tests.

## In your shell

`cdi [FOLDER]` goes to a folder you choose on the canvas. It opens on the folder you're in (or
FOLDER), what's in it beside; Enter goes to the folder you're on, Esc stays put. ⌃T puts a chosen
path in at the cursor, quoted. Add one line to your shell's rc file:

```bash
eval "$(python3 /path/to/treecanvas.py --init bash)"      # ~/.bashrc
eval "$(python3 /path/to/treecanvas.py --init zsh)"       # ~/.zshrc
python3 /path/to/treecanvas.py --init fish | source       # ~/.config/fish/config.fish
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
import treecanvas
path = treecanvas.pick('~/src/cd', dirs_only=True, theme='amber')   # absolute path, or None
```

A start that doesn't exist yet (`~/src/cd`) opens its folder filtered by the rest. Any flag can
be passed by name; `theme` may also be a dict (`{"base": "nord", "sel_bg": 31}`), `header` / `footer`
functions of the width returning a line for the top / bottom, `on_start=True` begins on the start
folder itself (for choosing a folder), `defaults` sets defaults that config.json can still change, and `skip` names config.json keys to ignore
(a host app keeping its own look passes `skip=('theme', 'colors')`).
