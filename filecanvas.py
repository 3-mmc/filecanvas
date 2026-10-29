#!/usr/bin/env python3
"""Prototype: the file system as one canvas you move across.

Every folder you open keeps its place on a tidy-tree layout (depth to the right, children
spread down, each block of children centred on its parent and shifted only as much as it
needs to keep clear of its neighbours). A camera eases after the selection and only moves
when the selection leaves a box around the middle of the screen. The path you're on is amber;
everything else dims with its distance from it.

    python3 filecanvas.py [START]          # prints the chosen path on stdout, nothing on Esc
    cd "$(python3 filecanvas.py)"

Try it from a plain bash (Ctrl-T puts the chosen path in at the cursor; normal Tab untouched):

    _canvas() { local p; p=$(python3 /path/to/filecanvas.py) || return
                READLINE_LINE=${READLINE_LINE:0:READLINE_POINT}$p${READLINE_LINE:READLINE_POINT}
                READLINE_POINT=$((READLINE_POINT + ${#p})); }
    bind -x '"\\C-t": _canvas'

or zsh:

    _canvas() { local p; p=$(python3 /path/to/filecanvas.py </dev/tty) && LBUFFER+=$p; zle reset-prompt }
    zle -N _canvas; bindkey '^T' _canvas

Keys: ↑↓ siblings · → or Tab open · ← parent (the branch stays open) · type to filter this
column · Backspace unfilters · ⌃X fold everything off the current path · Enter choose · Esc.
Tunables are flags (--help); try a few, they're what the prototype is for.
"""
import argparse, os, select, sys, time

SEL = "\033[48;5;214;38;5;232m"
AMBER = (222, 214, 178, 172, 136, 130, 94, 94, 58)
GREY = (252, 249, 246, 244, 242, 240, 239, 238, 237)
HIDDEN = 239


class Node:
    def __init__(self, path, name, is_dir, parent=None):
        self.path, self.name, self.is_dir, self.parent = path, name, is_dir, parent
        self.kids = None        # loaded lazily
        self.open = False
        self.sel = 0            # index into shown(): the remembered / selected child
        self.filter = ""
        self.error = None
        self.row = self.depth = 0

    def load(self):
        if self.kids is None:
            self.kids = []
            try:
                with os.scandir(self.path) as it:
                    for e in it:
                        try:
                            d = e.is_dir()
                        except OSError:
                            d = False
                        self.kids.append(Node(e.path, e.name, d, self))
            except OSError as e:
                self.error = e.strerror or "unreadable"
            self.kids.sort(key=lambda n: (not n.is_dir, n.name.startswith('.'), n.name.casefold()))
        return self.kids

    def shown(self):
        f = self.filter.casefold()
        return [k for k in self.load() if f in k.name.casefold()] if f else self.load()

    def label(self):
        return self.name + ('/' if self.is_dir and self.name != '/' else '')


class Marker:
    """A stand-in row: '↑ 12 more', '(empty)'."""
    is_dir, open, kids = False, False, []

    def __init__(self, text, parent):
        self.text, self.parent, self.row, self.depth = text, parent, 0, 0

    def label(self):
        return self.text


def window(node, limit):
    """The children drawn for an open node: at most `limit`, around its selected one."""
    items = node.shown()
    if not items:
        return [Marker('(' + (node.error or ('no match' if node.filter else 'empty')) + ')', node)]
    if not limit or len(items) <= limit:
        return items
    start = max(0, min(node.sel - limit // 2, len(items) - limit))
    out = items[start:start + limit]
    if start:
        out = [Marker(f'↑ {start} more', node)] + out[1:]
    if start + limit < len(items):
        out = out[:-1] + [Marker(f'↓ {len(items) - start - limit + 1} more', node)]
    return out


def is_open(n, focus, peek):
    return n.is_dir and (n.open or (peek and n is focus))


def layout(n, opts, focus):
    """Rows relative to `n` for its subtree; returns its contour: [(top, bottom)] per depth."""
    if not is_open(n, focus, opts.peek):
        return [(0, 0)]
    kids = window(n, opts.limit)
    n._shown = kids
    acc, ys = [], []
    for k in kids:
        c = layout(k, opts, focus)
        y = ys[-1] + 1 if ys else 0
        for d in range(min(len(acc), len(c))):
            # Keep clear of what's already placed at every depth; a blank row between cousins.
            y = max(y, acc[d][1] - c[d][0] + 1 + (1 if d else 0))
        for d, (t, b) in enumerate(c):
            if d < len(acc):
                acc[d] = (min(acc[d][0], t + y), max(acc[d][1], b + y))
            else:
                acc.append((t + y, b + y))
        ys.append(y)
    shift = -((ys[0] + ys[-1]) // 2)    # centred on the parent's row
    for k, y in zip(kids, ys):
        k._rel = y + shift
    return [(0, 0)] + [(t + shift, b + shift) for t, b in acc]


def place(n, row, depth, focus, opts, out):
    n.row, n.depth = row, depth
    out.append(n)
    if is_open(n, focus, opts.peek):
        for k in n._shown:
            place(k, row + k._rel, depth + 1, focus, opts, out)


class Canvas:
    def __init__(self, start, opts):
        self.opts = opts
        self.root = Node('/', '/', True)
        node = self.root
        for part in [p for p in os.path.abspath(start).split('/') if p]:
            node.open = True
            for i, k in enumerate(node.shown()):
                if k.name == part:
                    node.sel, node = i, k
                    break
        if node.is_dir:
            node.open = True
            self.focus = node.shown()[0] if node.shown() else node
        else:
            self.focus = node
        self.cam = None       # (x, y) world position of the top-left corner, floats
        self.target = None

    # -- moving
    def siblings(self):
        p = self.focus.parent
        return p.shown() if p else [self.focus]

    def move(self, step):
        p = self.focus.parent
        sib = self.siblings()
        if not p or not sib:
            return
        i = (sib.index(self.focus) + step) % len(sib) if self.focus in sib else 0
        if self.opts.fold and self.focus.open:
            self.focus.open = False
        p.sel, self.focus = i, sib[i]

    def enter(self):
        f = self.focus
        if not f.is_dir:
            return
        f.open = True
        kids = f.shown()
        if kids:
            f.sel = min(f.sel, len(kids) - 1)
            self.focus = kids[f.sel]

    def leave(self):
        if self.focus.parent:
            self.focus = self.focus.parent

    def refilter(self, text):
        p = self.focus.parent
        if not p:
            return
        p.filter = text
        sib = p.shown()
        if self.focus in sib:
            p.sel = sib.index(self.focus)
        elif sib:
            p.sel, self.focus = 0, sib[0]
        # nothing matches: focus stays where it was, the column shows "(no match)"

    def fold(self):
        on = set()
        n = self.focus
        while n:
            on.add(id(n)); n = n.parent
        stack = [self.root]
        while stack:
            n = stack.pop()
            if n.kids:
                stack.extend(n.kids)
            if id(n) not in on:
                n.open = False

    # -- drawing
    def frame(self, w, h, animate=True):
        o = self.opts
        layout(self.root, o, self.focus)
        nodes = []
        place(self.root, 0, 0, self.focus, o, nodes)
        widths = {}
        for n in nodes:
            widths[n.depth] = max(widths.get(n.depth, 0), min(o.colmax, len(n.label())))
        xs = [0]
        for d in range(max(widths) + 1):
            xs.append(xs[-1] + widths.get(d, 0) + o.gap)

        path = set()
        n = self.focus
        while n:
            path.add(id(n)); n = n.parent

        # Camera: keep the focus inside a box round the middle (a fraction of the view).
        view_h = h - 2
        fx, fy = xs[self.focus.depth], self.focus.row
        aim_x, aim_y = w * o.across, view_h / 2
        if self.cam is None:
            self.cam = (fx - aim_x, fy - aim_y)
        tx, ty = self.target or self.cam
        half_w, half_h = w * o.dead / 2, view_h * o.dead / 2
        sx, sy = fx - tx, fy - ty
        if not aim_x - half_w <= sx <= aim_x + half_w:
            tx = fx - (aim_x - half_w if sx < aim_x - half_w else aim_x + half_w)
        if not aim_y - half_h <= sy <= aim_y + half_h:
            ty = fy - (aim_y - half_h if sy < aim_y - half_h else aim_y + half_h)
        self.target = (tx, ty)
        cx, cy = self.cam
        if not animate or o.ease >= 1:
            cx, cy = tx, ty
        else:
            cx += (tx - cx) * o.ease
            cy += (ty - cy) * o.ease
            if abs(tx - cx) < 0.5 and abs(ty - cy) < 0.5:
                cx, cy = tx, ty
        self.cam = (cx, cy)
        ox, oy = round(cx), round(cy)

        grid = [[(' ', '')] * w for _ in range(view_h)]

        def put(x, y, text, style):
            y -= oy
            if 0 <= y < view_h:
                for i, ch in enumerate(text):
                    xx = x - ox + i
                    if 0 <= xx < w:
                        grid[y][xx] = (ch, style)

        def dist(n):
            d = 0
            while id(n) not in path and n.parent is not None:
                n, d = n.parent, d + 1
            return d

        def label_style(n):
            if n is self.focus:
                return SEL
            if id(n) in path:
                return f"\033[1;38;5;{AMBER[1]}m"
            d = dist(n)
            if n.parent is self.focus.parent and n.parent is not None and d == 1:
                # the focus's own column: a wheel, fading with distance from the choice
                return f"\033[38;5;{GREY[min(abs(n.row - self.focus.row) // o.fade, len(GREY) - 1)]}m"
            shade = GREY[min(2 + d * 2, len(GREY) - 1)]
            if isinstance(n, Node) and n.name.startswith('.'):
                shade = min(shade, HIDDEN)
            return f"\033[38;5;{shade}m"

        for n in nodes:
            x = xs[n.depth]
            text = n.label()
            if len(text) > o.colmax:
                text = text[:o.colmax - 1] + '…'
            st = label_style(n)
            put(x, n.row, (' ' + text + ' ') if n is self.focus else text, st)
            if is_open(n, self.focus, o.peek) and n._shown:
                kids = n._shown
                hot = id(n) in path and any(id(k) in path for k in kids)
                line = f"\033[38;5;{AMBER[3]}m" if hot else f"\033[38;5;{GREY[min(3 + dist(n) * 2, len(GREY) - 1)]}m"
                spine = xs[n.depth + 1] - 2
                end = x + min(len(n.label()), o.colmax) + (2 if n is self.focus else 1)
                put(end, n.row, '─' * max(0, spine - end), line)
                top, bot = kids[0].row, kids[-1].row
                rows = {k.row for k in kids}
                for y in range(min(top, n.row), max(bot, n.row) + 1):
                    if y == n.row:
                        ch = ('─' if top == bot else '┬' if y == top else '┴' if y == bot else '┼') if y in rows else '┤'
                    elif y in rows:
                        ch = '╭' if y == top else '╰' if y == bot else '├'
                    else:
                        ch = '│'
                    put(spine, y, ch, line)
                for k in kids:
                    hk = hot and id(k) in path
                    put(spine + 1, k.row, '─', f"\033[38;5;{AMBER[3]}m" if hk else line)
                # the amber part of the spine runs from the parent's row to the chosen child's
                if hot:
                    chosen = next(k for k in kids if id(k) in path)
                    for y in range(min(n.row, chosen.row), max(n.row, chosen.row) + 1):
                        ch, _ = grid[y - oy][spine - ox] if 0 <= y - oy < view_h and 0 <= spine - ox < w else (None, None)
                        if ch:
                            put(spine, y, ch, f"\033[38;5;{AMBER[1]}m")

        lines = []
        for row in grid:
            s, cur = [], None
            for ch, st in row:
                if st != cur:
                    s.append('\033[0m' + st); cur = st
                s.append(ch)
            lines.append(''.join(s) + '\033[0m')
        p = self.focus.parent
        filt = f"   \033[38;5;{AMBER[1]}mfilter: {p.filter}▏\033[0m" if p and p.filter else ''
        shown = display(self.focus.path)
        lines.append(f"\033[38;5;{AMBER[0]}m› {shown[-(w - 3):]}\033[0m{filt}")
        hint = '↑↓ move  →/tab open  ← parent  type filter  ⌃X fold  ⏎ choose  esc cancel'
        lines.append(f"\033[38;5;{GREY[5]}m{hint[:w - 1]}\033[0m")
        return lines, (ox, oy) != (round(tx), round(ty))


def display(path):
    cwd = os.getcwd()
    if path == cwd:
        return '.'
    if path.startswith(cwd.rstrip('/') + '/'):
        return os.path.relpath(path, cwd)
    home = os.path.expanduser('~')
    if path == home or path.startswith(home + '/'):
        return '~' + path[len(home):]
    return path


def keys(data):
    seq = {'\x1b[A': 'up', '\x1b[B': 'down', '\x1b[C': 'right', '\x1b[D': 'left',
           '\x1bOA': 'up', '\x1bOB': 'down', '\x1bOC': 'right', '\x1bOD': 'left', '\x1b[Z': 'backtab'}
    i = 0
    while i < len(data):
        for s, name in seq.items():
            if data.startswith(s, i):
                yield name; i += len(s); break
        else:
            yield 'esc' if data[i] == '\x1b' else data[i]
            i += 1


def apply(c, k, opts):
    """One key; returns ('done', path) / ('cancel', None) / None."""
    p = c.focus.parent
    if k in ('esc', '\x03', '\x07'):
        return 'cancel', None
    if k in ('\r', '\n'):
        return 'done', c.focus.path
    if k == 'up' or (k == 'backtab' and opts.tab == 'cycle'):
        c.move(-1)
    elif k == 'down' or (k == '\t' and opts.tab == 'cycle'):
        c.move(1)
    elif k in ('right', '\t'):
        c.enter()
    elif k == 'left':
        c.leave()
    elif k == '\x18':
        c.fold()
    elif k in ('\x7f', '\x08'):
        if p and p.filter:
            c.refilter(p.filter[:-1])
    elif k == '\x15':
        c.refilter('')
    elif len(k) == 1 and k.isprintable():
        c.refilter((p.filter if p else '') + k)
    return None


def run(opts):
    c = Canvas(opts.start, opts)
    tty = open('/dev/tty', 'r+b', buffering=0)
    fd = tty.fileno()
    import termios, tty as ttymod
    old = termios.tcgetattr(fd)

    def out(s):
        tty.write(s.encode())

    result = None
    try:
        ttymod.setraw(fd)
        out('\033[?1049h\033[?25l')
        animate = not os.environ.get('CDECK_CALM')
        size, dirty = None, True
        while True:
            if os.get_terminal_size(fd) != size:
                size, dirty = os.get_terminal_size(fd), True
            if dirty:
                lines, moving = c.frame(*size, animate)
                out('\033[?2026h\033[H' + '\r\n'.join(l + '\033[K' for l in lines) + '\033[?2026l')
                dirty = moving
            ready, _, _ = select.select([fd], [], [], 1 / 60 if moving else 0.25)
            if not ready:
                continue
            dirty = True
            data = os.read(fd, 1024).decode(errors='replace')
            if data == '\x1b':  # a lone Esc, or the start of an arrow split across reads
                more, _, _ = select.select([fd], [], [], 0.03)
                if more:
                    data += os.read(fd, 1024).decode(errors='replace')
            for k in keys(data):
                r = apply(c, k, opts)
                if r:
                    result = r
                    break
            if result:
                break
    finally:
        out('\033[?25h\033[?1049l')
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if result and result[0] == 'done':
        print(display(result[1]))
        return 0
    return 1


def dump(opts):
    """Headless: apply --keys, print the last frame as plain text (for checking layout)."""
    import re
    c = Canvas(opts.start, opts)
    for k in keys(opts.keys.encode().decode('unicode_escape')):
        if apply(c, k, opts):
            break
    w, h = map(int, opts.size.split('x'))
    lines, _ = c.frame(w, h, animate=False)
    for l in lines:
        print(re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', l).rstrip())


if __name__ == '__main__':
    a = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    a.add_argument('start', nargs='?', default='.')
    a.add_argument('--limit', type=int, default=0, help='most children shown per folder, the rest as "↓ N more"; 0 = all, trailing off screen (0)')
    a.add_argument('--dead', type=float, default=0.3, help='dead-zone size as a fraction of the view; 0 = always centred (0.3)')
    a.add_argument('--across', type=float, default=0.4, help='where the focus sits across the view, 0..1 (0.4)')
    a.add_argument('--ease', type=float, default=0.3, help='camera easing per frame; 1 = jump (0.3)')
    a.add_argument('--fade', type=int, default=2, help='rows per grey step in the focused column (2)')
    a.add_argument('--colmax', type=int, default=24, help='widest a column gets (24)')
    a.add_argument('--gap', type=int, default=5, help='columns between a name and its children (5)')
    a.add_argument('--peek', action='store_true', help="show the focused folder's contents before opening it")
    a.add_argument('--fold', action='store_true', help='close a branch when you move off it (classic tree)')
    a.add_argument('--tab', choices=('branch', 'cycle'), default='branch', help='Tab opens (branch) or moves down (cycle)')
    a.add_argument('--keys', help=argparse.SUPPRESS)
    a.add_argument('--size', default='120x36', help=argparse.SUPPRESS)
    opts = a.parse_args()
    if opts.keys is not None:
        dump(opts)
    else:
        sys.exit(run(opts))
