"""Several providers as one tree: their top-level nodes side by side under one root, and every
other node answered by the provider whose KINDS has its kind. Each one's invalidate() comes
here, so the canvas, which only knows this one, hears it."""
from . import filecanvas as fc


class Together(fc.Provider):
    pane = True

    def __init__(self, providers):
        self.providers = providers
        for p in providers:
            p.invalidate = self.invalidate      # theirs is ours: the canvas asks only us,
            p.signal = self.signal              # for what changed and for what moves (pulse, status)

    def owner(self, node):
        return next((p for p in self.providers if node.kind in p.KINDS), None)

    def root(self):
        return fc.Node('', 'msgcanvas', True, source=self, kind='root')

    def children(self, node):
        if node.kind == 'root':
            out = []
            for p in self.providers:
                try:
                    out += p.children(node)
                except OSError as e:     # one that can't start (no nchat) doesn't hide the rest
                    out.append(fc.Node('/!' + p.root().name, p.root().name, False, node, 'broken',
                                       data=e.strerror or str(e)))
            return out
        return self.owner(node).children(node)

    def locate(self, start):
        return self.providers[0].locate(start)

    def ask(self, name, node, default, *args):
        p = self.owner(node)
        return getattr(p, name)(node, *args) if p else default

    def label(self, node):
        return self.ask('label', node, node.name)

    def colour(self, node):
        return self.ask('colour', node, None)

    def mark(self, node):
        return ('git_conflict', '✖', 'x') if node.kind == 'broken' else self.ask('mark', node, None)

    def dim(self, node):
        return 0.6 if node.kind == 'broken' else self.ask('dim', node, 1.0)

    def bar(self, node, plain):
        p = self.owner(node)
        return p.bar(node, plain) if p else super().bar(node, plain)

    def preview(self, node):
        if node.kind == 'broken':
            return node.data
        return self.ask('preview', node, None)

    def preview_end(self, node):
        return self.ask('preview_end', node, False)

    def actions(self, node):
        return self.ask('actions', node, [])

    def act(self, node, name, canvas):
        return self.ask('act', node, None, name, canvas)

    def busy(self):
        return any(p.busy() for p in self.providers)

    def choose(self, node):
        return None
