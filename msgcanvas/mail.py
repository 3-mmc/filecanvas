"""Mail on the canvas: accounts, their folders, the threads in a folder (newest first), and each
thread's messages as the tree of who replied to whom. The reading pane shows the one you're on.

Read from notmuch's index of the maildirs mbsync keeps (accounts.py); nothing here changes mail
yet. A thread watches notmuch's revision, so mail that `msgcanvas sync` brings in appears."""
import email, email.policy, email.utils, html.parser, os, re, threading, time

import notmuch2

from . import filecanvas as fc

# Folders shown first, in this order, by the start of their name in English, German or Czech.
ORDER = (('INBOX',), ('Sent', 'Gesendet', 'Odeslan'), ('Drafts', 'Entwürfe', 'Koncept'),
         ('Archive', 'Archiv'), ('Starred', 'Markiert', 'S hvězdičkou'),
         ('Junk', 'Spam', 'Nevyžádan'), ('Trash', 'Deleted', 'Gelöscht', 'Papierkorb', 'Koš'))
SEP = '\x1f'     # in ids: between a folder and a thread, and a thread and a message


def database():
    """notmuch, read-only, with msgcanvas's config (NOTMUCH_CONFIG)."""
    return notmuch2.Database(mode=notmuch2.Database.MODE.READ_ONLY)


def quote(text):
    """A term for a notmuch query, in double quotes."""
    return '"' + text.replace('"', '""') + '"'


def when(stamp, now=None):
    """A short date: the time today, the day this year, the day and year before."""
    t, now = time.localtime(stamp), time.localtime(now or time.time())
    if t[:3] == now[:3]:
        return time.strftime('%H:%M', t)
    day = f'{t.tm_mday} {time.strftime("%b", t)}'
    return day if t.tm_year == now.tm_year else f'{day} {t.tm_year}'


def who(header):
    """The name in a From line, else the address's mailbox."""
    name, addr = email.utils.parseaddr(header or '')
    return name or addr.split('@')[0] or '(nobody)'


def folder_name(rel):
    """How a folder is shown: Inbox, and Gmail's [Gmail]/ dropped."""
    if rel == 'INBOX':
        return 'Inbox'
    return rel[len('[Gmail]/'):] if rel.startswith('[Gmail]/') else rel


def folder_rank(rel):
    name = folder_name(rel)
    hit = next((i for i, names in enumerate(ORDER)
                if any(name.casefold().startswith(o.casefold()) for o in names)), len(ORDER))
    return hit, name.casefold()


class Text(html.parser.HTMLParser):
    """HTML as plain text, enough to read a message by."""
    BLOCK = {'p', 'div', 'br', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'table', 'hr'}
    HIDDEN = {'script', 'style', 'head', 'title'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append('\n')

    def handle_endtag(self, tag):
        if tag in self.HIDDEN:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.out.append(re.sub(r'\s+', ' ', data))

    def text(self):
        lines = [l.strip() for l in ''.join(self.out).split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def html_text(markup):
    t = Text()
    t.feed(markup)
    t.close()
    return t.text()


def read(path):
    """A message file as text for the pane: its headers, its body, what's attached."""
    try:
        with open(path, 'rb') as f:
            msg = email.message_from_binary_file(f, policy=email.policy.default)
    except OSError as e:
        return f'({e.strerror or "unreadable"})'
    head = [f'{k}: {msg[k]}' for k in ('From', 'To', 'Cc', 'Date', 'Subject') if msg[k]]
    part = msg.get_body(preferencelist=('plain', 'html'))
    body = ''
    if part is not None:
        try:
            body = part.get_content()
        except (LookupError, ValueError):
            body = (part.get_payload(decode=True) or b'').decode('utf-8', 'replace')
        if part.get_content_type() == 'text/html':
            body = html_text(body)
    files = [f'📎 {a.get_filename() or a.get_content_type()} '
             f'({len(a.get_payload(decode=True) or b"") / 1024:,.0f} KB)' for a in msg.iter_attachments()]
    return '\n'.join(head) + '\n\n' + body.strip() + ('\n\n' + '\n'.join(files) if files else '')


def header(m, name):
    try:
        return m.header(name)
    except LookupError:
        return ''


def message(m):
    """What a node keeps of a notmuch message and its replies, taken while the database is open."""
    tags = set(m.tags)
    return dict(mid=m.messageid, path=str(m.path), date=m.date, author=who(header(m, 'From')),
                subject=header(m, 'Subject'), unread='unread' in tags, flagged='flagged' in tags,
                replies=[message(r) for r in m.replies()])


def count(msgs):
    return sum(1 + count(m['replies']) for m in msgs)


def any_of(msgs, key):
    return any(m[key] or any_of(m['replies'], key) for m in msgs)


class Mail(fc.Provider):
    """Accounts (accounts.Account) → folders → threads → messages, each with its replies."""
    pane = True

    def __init__(self, accounts, watch=2.0):
        self.accounts = accounts
        self._seen = None
        if watch:
            threading.Thread(target=self.watch, args=(watch,), daemon=True).start()

    def watch(self, every):
        """Invalidate everything when notmuch's index changes (new mail, changed tags)."""
        while True:
            time.sleep(every)
            try:
                with database() as db:
                    r = db.revision()
                    rev = (r.rev, r.uuid)
            except Exception:       # notmuch busy or not there yet: a watcher that died would say nothing
                continue
            if self._seen is not None and rev != self._seen:
                self.invalidate()
            self._seen = rev

    # -- the tree
    def root(self):
        return fc.Node('', 'mail', True, source=self, kind='root')

    def children(self, node):
        try:
            if node.kind == 'root':
                return [fc.Node('/' + a.name, a.name, True, node, 'account', data=a) for a in self.accounts]
            if node.kind == 'account':
                return self.folders(node)
            if node.kind == 'folder':
                return self.threads(node)
            return [self.message_node(m, node) for m in
                    (node.data['messages'] if node.kind == 'thread' else node.data['replies'])]
        except notmuch2.NotmuchError as e:
            raise OSError(0, f'notmuch: {e}') from e

    def folders(self, node):
        a, out = node.data, []
        for top, dirs, _ in os.walk(a.maildir):
            dirs.sort()
            if os.path.isdir(os.path.join(top, 'cur')):
                out.append(os.path.relpath(top, a.maildir))
            dirs[:] = [d for d in dirs if d not in ('cur', 'new', 'tmp')]
        out.sort(key=folder_rank)
        with database() as db:
            root = db.path if isinstance(db.path, str) else str(db.path)
            nodes = []
            for rel in out:
                q = 'folder:' + quote(os.path.relpath(os.path.join(a.maildir, rel), root))
                data = dict(query=q, rel=rel, total=db.count_messages(q),
                            unread=db.count_messages(q + ' and tag:unread'))
                nodes.append(fc.Node(f'{node.path}/{rel}', folder_name(rel), True, node, 'folder', data=data))
        if not nodes:
            raise OSError(0, 'no mail yet: msgcanvas sync')
        return nodes

    def threads(self, node):
        out = []
        with database() as db:
            for t in db.threads(node.data['query'], sort=notmuch2.Database.SORT.NEWEST_FIRST):
                msgs = [message(m) for m in t.toplevel()]
                data = dict(tid=t.threadid, subject=t.subject or '(no subject)', date=t.last,
                            authors=t.authors or '', messages=msgs, count=count(msgs),
                            unread=any_of(msgs, 'unread'), flagged=any_of(msgs, 'flagged'))
                # A thread of one message is that message: nothing to open.
                out.append(fc.Node(f'{node.path}{SEP}{t.threadid}', data['subject'], data['count'] > 1,
                                   node, 'thread', data=data))
        return out

    def message_node(self, m, parent):
        return fc.Node(f'{parent.path}{SEP}{m["mid"]}', f'{m["author"]} · {when(m["date"])}',
                       bool(m['replies']), parent, 'message', data=m)

    def locate(self, start):
        """`account` or `account/folder`."""
        parts = [p for p in (start or '').split('/', 1) if p and p != '.']
        ids = []
        if parts:
            ids.append('/' + parts[0])
        if len(parts) > 1:
            ids.append(f'/{parts[0]}/{parts[1]}')
        return ids, ''

    # -- how it looks
    def label(self, node):
        d = node.data
        if node.kind == 'folder' and d['unread']:
            return f'{node.name} {d["unread"]}'
        if node.kind == 'thread' and d['count'] > 1:
            return f'{node.name} ({d["count"]})'
        return node.name

    def unread(self, node):
        d = node.data
        return bool(d and node.kind in ('folder', 'thread', 'message') and d['unread'])

    def colour(self, node):
        return (('dir' if node.is_dir else 'file'), True) if self.unread(node) else None

    def mark(self, node):
        if node.kind in ('thread', 'message'):
            if node.data['unread']:
                return 'git_dirty', '●', '*'
            if node.data['flagged']:
                return 'git_staged', '★', '!'
        return None

    def dim(self, node):
        return 0.8 if node.kind == 'folder' and not node.data['total'] else 1.0

    def bar(self, node, plain):
        names, n = [], node     # names, not labels: no counts or dates
        while n.parent:
            names.append(n.data['author'] if n.kind == 'message' else n.name); n = n.parent
        names.reverse()
        segs = [('mail', 'home_fg', 'home_bg', False)] + [
            (t, 'cwd_fg' if i == len(names) - 1 else 'seg_fg', 'seg_bg', i == len(names) - 1)
            for i, t in enumerate(names)]
        tail = []
        f = node
        while f is not None and f.kind != 'folder':
            f = f.parent
        if f is not None:
            d = f.data
            tail.append((f'{d["unread"]} unread' if d['unread'] else f'{d["total"]} messages',
                         'repo_dirty_fg' if d['unread'] else 'repo_clean_fg',
                         'repo_dirty_bg' if d['unread'] else 'repo_clean_bg', False))
        return segs, tail

    # -- the reading pane
    def preview(self, node):
        d = node.data
        if node.kind == 'account':
            return f'{d.address}\n{d.type}, {d.imap_host}\n\n{d.maildir}'
        if node.kind == 'folder':
            return f'{d["total"]:,} messages, {d["unread"]:,} unread'
        if node.kind == 'thread' and d['count'] == 1:
            return read(d['messages'][0]['path'])
        if node.kind == 'thread':
            lines = [f'{d["count"]} messages, {d["authors"]}', '']

            def walk(msgs, depth):
                for m in msgs:
                    lines.append(f'{"  " * depth}{"●" if m["unread"] else "·"} {m["author"]}, {when(m["date"])}')
                    walk(m['replies'], depth + 1)
            walk(d['messages'], 0)
            return '\n'.join(lines)
        if node.kind == 'message':
            return read(d['path'])
        return None

    def choose(self, node):
        return None      # a mail client stays open: Enter opens, Esc leaves
