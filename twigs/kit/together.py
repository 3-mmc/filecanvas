"""Several providers as one tree, each a section of it: a named branch from the root (Emails,
Messages, Calendar, TuiDo) holding what that provider's own root would. Every node below belongs to
the provider that made it (its `source`), which answers for it; each one's invalidate() and
signal() come here, so the canvas, which only knows this one, hears them."""
from twigs import core as fc


class Together(fc.Provider):
    pane = True

    def __init__(self, sections):
        """`sections`: [(name, provider)], in the order they branch from the root."""
        self.sections = list(sections)
        self.providers = [p for _, p in self.sections]
        for p in self.providers:
            p.invalidate = self.invalidate      # theirs is ours: the canvas asks only us,
            p.signal = self.signal              # for what changed and for what moves (pulse, status)

    def owner(self, node):
        """The provider a node belongs to: the one that made it (a section is its provider's)."""
        src = node.source
        return src if src is not self and any(src is p for p in self.providers) else None

    def root(self):
        return fc.Node('', 'twigs', True, source=self, kind='root')

    def section(self, name, p, parent):
        """A section: its provider's own root, named, so the provider's children() takes it as one."""
        r = p.root()
        return fc.Node(r.path or f'§{name}', name, True, parent, r.kind, source=p, data=r.data)

    def children(self, node):
        if node.source is self:
            return [self.section(name, p, node) for name, p in self.sections]
        return node.source.children(node)

    def locate(self, start):
        """The first section's own place for `start` (an account, a folder), inside it."""
        if not self.sections:
            return [], ''
        name, p = self.sections[0]
        ids, typed = p.locate(start)
        top = p.root().path or f'§{name}'
        return ([top] + [i for i in ids if i != top] if ids else []), typed

    def is_section(self, node):
        return node.parent is not None and node.parent.source is self

    def ask(self, name, node, default, *args):
        p = self.owner(node)
        return getattr(p, name)(node, *args) if p else default

    def label(self, node):
        return node.name if self.is_section(node) else self.ask('label', node, node.name)

    def colour(self, node):
        return self.ask('colour', node, None)

    def mark(self, node):
        return self.ask('mark', node, None)

    def dim(self, node):
        return self.ask('dim', node, 1.0)

    def bar(self, node, plain):
        p = self.owner(node)
        return p.bar(node, plain) if p else super().bar(node, plain)

    def preview(self, node):
        return self.ask('preview', node, None)

    def tint(self, node):
        return self.ask('tint', node, [])

    def beacon(self, node):
        return self.ask('beacon', node, None)

    def glow(self, node):
        return self.ask('glow', node, None)

    def next_change(self):
        """The soonest any of them changes by itself."""
        got = [t for t in (p.next_change() for p in self.providers) if t is not None]
        return min(got) if got else None

    def mark_before(self, node):
        return self.ask('mark_before', node, False)

    def line(self, node):
        return self.ask('line', node, None)

    def count(self, node):
        return self.ask('count', node, None)

    def width(self, node):
        return self.ask('width', node, None)

    def chip(self, node):
        return self.ask('chip', node, None)

    def start_at(self, node):
        return self.ask('start_at', node, 0)

    def pane_for(self, node):
        p = self.owner(node)                    # an app with no reading pane (tuido's desk) never opens one
        return bool(p and getattr(p, 'pane', False) and p.pane_for(node))

    def preview_end(self, node):
        return self.ask('preview_end', node, False)

    def actions(self, node):
        return self.ask('actions', node, [])

    def act(self, node, name, canvas):
        return self.ask('act', node, None, name, canvas)

    def focus(self, node):
        self.ask('focus', node, None)

    def busy(self):
        return any(p.busy() for p in self.providers)

    def choose(self, node):
        return self.ask('choose', node, None)
