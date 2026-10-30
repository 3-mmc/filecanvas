"""Later: what's waiting for a time you said (twigs.kit.when): threads snoozed till then, drafts to
be sent then. Kept in a JSON file in msgcanvas's state; nothing is written to notmuch or the
server."""
import json, os

from twigs.kit.when import EVENING, MORNING, SAYS, parse, say     # noqa: F401  (mail's words for them)


class Later:
    """{'snoozed': {thread id: [until, account key, folder]}, 'woke': {thread id: [when it came
    back, account key, folder]}, 'sending': {draft path: [when, account key]}}, in a JSON file
    (None: kept in memory)."""
    def __init__(self, path):
        self.path = path
        try:
            with open(path) as f:
                got = json.load(f)
        except (TypeError, OSError, ValueError):
            got = {}
        self.snoozed, self.woke, self.sending = (got.get(k, {}) for k in ('snoozed', 'woke', 'sending'))

    def save(self):
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path + '.new', 'w') as f:
            json.dump({'snoozed': self.snoozed, 'woke': self.woke, 'sending': self.sending}, f, indent=1)
        os.replace(self.path + '.new', self.path)

    def due(self, now):
        """(threads whose snooze is over, drafts whose time has come), by `now` (epoch seconds)."""
        return ([t for t, v in self.snoozed.items() if v[0] <= now],
                [p for p, v in self.sending.items() if v[0] <= now])

    def next(self):
        """The soonest anything here is due (epoch seconds), or None."""
        got = [v[0] for v in self.snoozed.values()] + [v[0] for v in self.sending.values()]
        return min(got) if got else None
