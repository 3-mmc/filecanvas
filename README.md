# filecanvas

A prototype file picker for the terminal: the file system as one canvas you move across.

Every folder you open keeps its place on a tree that branches right, each folder's children
centred on its row and spreading up and down only as far as they need. The camera eases after
your selection and keeps it near the middle; everything you've opened stays on screen, dimmed by
its distance from the path you're on (amber).

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
| `--dead F` | 0.3 | camera dead zone as a fraction of the view; `0` keeps the selection centred |
| `--across F` | 0.4 | where the selection sits across the view, 0..1 |
| `--ease F` | 0.3 | camera easing per frame; `1` jumps (so does `CDECK_CALM=1`) |
| `--fade N` | 2 | rows per grey step in the column you're moving in |
| `--colmax N` | 24 | widest a column gets |
| `--gap N` | 5 | columns between a name and its children |
| `--limit N` | 0 | most children per folder, the rest as "↓ N more"; `0` shows all, trailing off screen |
| `--peek` | off | show the selected folder's contents before opening it |
| `--fold` | off | close a branch when you move off it |
| `--tab cycle` | branch | Tab moves down instead of opening |

## In your shell

bash (⌃T inserts the chosen path at the cursor; Tab is left alone):

```bash
_filecanvas() { local p; p=$(python3 /path/to/filecanvas.py) || return
                READLINE_LINE=${READLINE_LINE:0:READLINE_POINT}$p${READLINE_LINE:READLINE_POINT}
                READLINE_POINT=$((READLINE_POINT + ${#p})); }
bind -x '"\C-t": _filecanvas'
```

zsh:

```zsh
_filecanvas() { local p; p=$(python3 /path/to/filecanvas.py </dev/tty) && LBUFFER+=$p; zle reset-prompt }
zle -N _filecanvas; bindkey '^T' _filecanvas
```

These snippets haven't been tried by hand yet.

Started as a prototype for [cdeck](https://github.com/vmagierski/cdeck)'s folder trail.
