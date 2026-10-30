"""Mail on the canvas: accounts, their folders, the threads in a folder (newest first), and each
thread's messages as the tree of who replied to whom. The reading pane shows the one you're on.

Read from notmuch's index of the maildirs mbsync keeps (accounts.py); nothing here changes mail
yet. A thread watches notmuch's revision, so mail that `msgcanvas sync` brings in appears."""
import email, email.policy, email.utils, hashlib, html.parser, json, os, re, subprocess, threading, time, zlib

import notmuch2

from . import compose, files, treecanvas as fc

# Folders shown first, in this order, by the start of their name in English, German or Czech.
BODY_WIDTH = 66       # columns a message's body wraps to on the canvas
BODY_LINES = 40       # lines of a body shown at first; ↓ more shows MORE_LINES more
MORE_LINES = 80

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


def when_group(stamp, now=None):
    """Which group a thread whose newest message came at `stamp` is in, by date."""
    t, now = time.localtime(stamp), time.localtime(now or time.time())
    day = lambda x: time.mktime((x.tm_year, x.tm_mon, x.tm_mday, 0, 0, 0, 0, 0, -1))
    today = day(now)
    since = today - day(t)
    if since <= 0:
        return 'Today'
    if since <= 86400 + 3600:                   # (a summer-time change makes a day 23 or 25 hours)
        return 'Yesterday'
    monday = today - now.tm_wday * 86400
    if stamp >= monday:
        return 'This week'
    if stamp >= monday - 7 * 86400:
        return 'Last week'
    if (t.tm_year, t.tm_mon) == (now.tm_year, now.tm_mon):
        return 'Earlier this month'
    if t.tm_year == now.tm_year:
        return time.strftime('%B', t)
    return str(t.tm_year)


def first_author(authors):
    """Who began a thread, from notmuch's authors ('Ann, Bo| Cy': matched ones before the |)."""
    return (authors or '').replace('|', ',').split(',')[0].strip() or '(nobody)'


def body_text(msg):
    """A message's text: its plain part, else its HTML as text."""
    part = msg.get_body(preferencelist=('plain', 'html'))
    if part is None:
        return ''
    try:
        body = part.get_content()
    except (LookupError, ValueError):
        body = (part.get_payload(decode=True) or b'').decode('utf-8', 'replace')
    if part.get_content_type() == 'text/html':
        body = html_text(body)
    return body.replace('\r', '').strip()


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
    """Accounts (accounts.Account) → folders → groups (by date, by sender, or none) → threads →
    messages, each with its replies; the message you're on opens onto what it says, on the
    canvas: its subject, who it's from and to (in their colours), when, its body, what's attached.
    `KINDS` are the node kinds it answers for."""
    KINDS = ('account', 'folder', 'part', 'group', 'thread', 'message', 'subject', 'person', 'when', 'body', 'more',
             'attachment', 'drafts', 'draft')
    pane = True             # only for a message's pictures (pane_for)
    GROUPINGS = ('date', 'sender', 'all')
    # how a folder splits before it groups: not, into Unread / Read, or into Flagged / The rest, a
    # thread at a time (one with any unread message is unread, whole)
    SPLITS = {'none': (), 'unread': (('Unread', 'thread:{tag:unread}'), ('Read', 'not thread:{tag:unread}')),
              'flagged': (('Flagged', 'thread:{tag:flagged}'), ('The rest', 'not thread:{tag:flagged}'))}

    def __init__(self, accounts, watch=2.0, pick=None, notify=True, grouping='date', remember=None, split='none'):
        self.accounts, self.notify = accounts, notify
        self.pick = pick or (lambda start: fc.pick(start, glyphs='plain'))   # a file, to attach
        self.remember_in = remember             # a file the grouping is kept in, from one time to the next
        self.grouping = self.recall('grouping', self.GROUPINGS) or grouping
        self.split = self.recall('split', self.SPLITS) or split
        self.lines = {}                         # a message's path: how many lines of its body show
        self.parsed = {}                        # a message's path: what's in it, read once
        self._seen = None
        if watch:
            threading.Thread(target=self.watch, args=(watch,), daemon=True).start()

    def recall(self, what, allowed):
        try:
            with open(self.remember_in) as f:
                got = json.load(f).get(what)
            return got if got in allowed else None
        except (TypeError, OSError, ValueError):
            return None

    def keep(self):
        if self.remember_in:
            try:
                with open(self.remember_in, 'w') as f:
                    json.dump({'grouping': self.grouping, 'split': self.split}, f)
            except OSError:
                pass

    def loading(self):
        """(what's loading, whether it's done), for the opening screen."""
        n = len(self.accounts)
        return f'{n} mail account{"s" if n != 1 else ""}', True

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
                if rev[1] == self._seen[1]:         # the same database: what came since
                    self.arrived(self._seen[0], rev[0])
            self._seen = rev

    def arrived(self, before, now):
        """New unread mail in an inbox, since revision `before`: the account and folder pulse, a
        note says what came, and (with notify) a desktop notification, one for all of it."""
        try:
            with database() as db:
                root = str(db.path)
                q = f'lastmod:{before + 1}..{now} and tag:unread and tag:inbox and date:3d..'
                new = [(str(m.path), who(header(m, 'From')), header(m, 'Subject'))
                       for m in db.messages(q, sort=notmuch2.Database.SORT.NEWEST_FIRST)]
        except notmuch2.NotmuchError:
            return
        if not new:
            return
        mine = {a.key for a in self.accounts}
        for path, _, _ in new:
            rel = os.path.relpath(path, root).split(os.sep)
            if rel[0] in mine and len(rel) > 2:          # account / folder… / cur|new / file
                self.pulse('/' + rel[0])
                self.pulse(f'/{rel[0]}/{os.path.join(*rel[1:-2])}')
        _, sender, subject = new[0]
        line = f'{sender}: {subject or "(no subject)"}'
        self.status('mail-new', line if len(new) == 1 else f'{len(new)} new mails · {line}', 'note')
        if self.notify:
            files.notify('New mail' if len(new) == 1 else f'{len(new)} new mails', line)

    # -- the tree
    def root(self):
        return fc.Node('', 'mail', True, source=self, kind='root')

    def children(self, node):
        try:
            k = node.kind
            if k == 'root':
                return [fc.Node('/' + a.key, a.name, True, node, 'account', data=a) for a in self.accounts]
            if k == 'account':
                return self.folders(node)
            if k == 'folder' and self.split != 'none':
                return self.parts(node)
            if k in ('folder', 'part'):
                return self.groups(node) if self.grouping != 'all' else self.threads(node, node.data['query'])
            if k == 'group':
                return self.threads(node, node.data['query'], node.data['tids'])
            if k == 'drafts':
                return [self.draft_node(path, node) for path in compose.drafts(node.parent.data)]
            if k == 'thread' and node.data['count'] == 1:
                return self.contents(node, node.data['messages'][0])
            if k == 'thread':
                return [self.message_node(m, node) for m in node.data['messages']]
            if k == 'message':
                return self.contents(node, node.data) + [self.message_node(m, node) for m in node.data['replies']]
            return []
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
        kept = compose.drafts(a)
        if kept:
            nodes.append(fc.Node(f'{node.path}{SEP}drafts', 'Unsent', True, node, 'drafts', data=dict(count=len(kept))))
        if not nodes:
            raise OSError(0, 'no mail yet: msgcanvas sync')
        return nodes

    def parts(self, node):
        """A folder split in two before it's grouped (Unread / Read, or Flagged / The rest); a part
        with nothing in it isn't shown."""
        out = []
        with database() as db:
            for name, cond in self.SPLITS[self.split]:
                q = f'({node.data["query"]}) and {cond}'
                total = db.count_messages(q)
                if total:
                    out.append(fc.Node(f'{node.path}{SEP}{self.split}:{name}', name, True, node, 'part',
                                       data=dict(query=q, total=total, unread=db.count_messages(q + ' and tag:unread'),
                                                 flagged=name == 'Flagged')))
        if not out:
            raise OSError(0, 'empty')
        return out

    def groups(self, node):
        """A folder's threads in groups: by when the newest message in each came (Today, Yesterday,
        This week…, each month this year, each year before), or by who wrote first. One pass over
        the threads' dates and authors, nothing else read; a group's threads load when it opens."""
        found = {}                              # group: [(newest, tid, unread)]
        with database() as db:
            for t in db.threads(node.data['query'], sort=notmuch2.Database.SORT.NEWEST_FIRST):
                tags = set(t.tags)
                key = when_group(t.last) if self.grouping == 'date' else first_author(t.authors)
                found.setdefault(key, []).append((t.last, t.threadid, 'unread' in tags))
        if not found:
            raise OSError(0, 'empty')
        order = sorted(found, key=lambda g: -found[g][0][0])                 # the newest group first
        return [fc.Node(f'{node.path}{SEP}{self.grouping}:{g}', g, True, node, 'group',
                        data=dict(query=node.data['query'], tids=[tid for _, tid, _ in found[g]],
                                  unread=sum(u for _, _, u in found[g])))
                for g in order]

    def threads(self, node, query, tids=None):
        out = []
        with database() as db:
            queries = [query] if tids is None else \
                [f'({query}) and (' + ' or '.join(f'thread:{t}' for t in tids[i:i + 100]) + ')'
                 for i in range(0, len(tids), 100)]
            seen = []
            for q in queries:
                seen += list(db.threads(q, sort=notmuch2.Database.SORT.NEWEST_FIRST))
            for t in sorted(seen, key=lambda t: -t.last) if tids is not None else seen:
                msgs = [message(m) for m in t.toplevel()]
                data = dict(tid=t.threadid, subject=t.subject or '(no subject)', date=t.last,
                            authors=t.authors or '', messages=msgs, count=count(msgs),
                            unread=any_of(msgs, 'unread'), flagged=any_of(msgs, 'flagged'))
                out.append(fc.Node(f'{node.path}{SEP}{t.threadid}', data['subject'], True, node, 'thread', data=data))
        return out

    def contents(self, node, m):
        """What a message says, as branches beside it: its subject, who it's from and to, when, its
        body (the first part of a long one: Enter on '↓ more' shows more), and what's attached."""
        got = self.parse(m['path'])
        base, out = node.path + SEP, []

        def add(kind, key, label, data=None):
            out.append(fc.Node(base + key, label, False, node, kind, data=data))
        add('subject', 'subject', got['subject'] or '(no subject)')
        people = [('from', p) for p in got['from']] + [('to', p) for p in got['to'][:6]] + \
                 [('cc', p) for p in got['cc'][:4]]
        for i, (role, (name, addr)) in enumerate(people):
            add('person', f'p{i}', name or addr, dict(role=role, address=addr))
        hidden = max(0, len(got['to']) - 6) + max(0, len(got['cc']) - 4)
        if hidden:
            add('person', 'p+', f'and {hidden} more', dict(role='', address=''))
        add('when', 'when', got['date'])
        shown = self.lines.get(m['path'], BODY_LINES)
        lines, paragraph, n = got['body'].split('\n'), [], 0
        for line in lines[:shown] + ['']:
            if line.strip():
                paragraph.append(line.rstrip())
            elif paragraph:
                add('body', f'b{n}', '\n'.join(paragraph))
                paragraph, n = [], n + 1
        if len(lines) > shown:
            add('more', 'more', f'↓ {len(lines) - shown} more lines', dict(path=m['path']))
        for i, (name, size, path) in enumerate(got['files']):
            add('attachment', f'a{i}', f'📎 {name}', dict(size=size, message=m['path'], index=i))
        return out

    def parse(self, path):
        """A message file, read once: subject, from/to/cc as (name, address), date, body, files."""
        if path in self.parsed:
            return self.parsed[path]
        try:
            with open(path, 'rb') as f:
                msg = email.message_from_binary_file(f, policy=email.policy.default)
        except OSError as e:
            got = dict(subject='', **{k: [] for k in ('from', 'to', 'cc')}, date='', files=[],
                       body=f'({e.strerror or "unreadable"})')
            return self.parsed.setdefault(path, got)
        people = lambda h: [(n, a) for n, a in email.utils.getaddresses([msg[h] or '']) if a or n]
        stamp = email.utils.parsedate_to_datetime(msg['Date']) if msg['Date'] else None
        got = dict(subject=str(msg['Subject'] or ''), **{h.lower(): people(h) for h in ('From', 'To', 'Cc')},
                   date=stamp.astimezone().strftime('%a %-d %b %Y, %H:%M') if stamp else '',
                   body=body_text(msg),
                   files=[(a.get_filename() or a.get_content_type(), len(a.get_payload(decode=True) or b''), path)
                          for a in msg.iter_attachments()])
        self.parsed[path] = got
        return got

    def draft_node(self, path, parent):
        headers, attach, _ = compose.parse(path)
        to = ', '.join(who(a) for a in headers.get('To', '').split(',') if a.strip()) or 'nobody yet'
        return fc.Node(f'{parent.path}{SEP}{os.path.basename(path)}', f'{headers.get("Subject") or "(no subject)"} → {to}',
                       False, parent, 'draft', data=dict(path=path, attach=attach))

    def message_node(self, m, parent):
        return fc.Node(f'{parent.path}{SEP}{m["mid"]}', f'{m["author"]} · {when(m["date"])}',
                       True, parent, 'message', data=m)

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
        if node.kind == 'drafts':
            return f'{node.name} {node.data["count"]}'
        return node.name

    def count(self, node):
        """Numbers in a column: a folder's unread, a group's threads, a thread's messages; beside
        a person, their address."""
        k, d = node.kind, node.data
        if k == 'folder':
            return (f'{d["unread"]:,}', 'git_dirty', True) if d['unread'] else None
        if k == 'group':
            return f'{len(d["tids"]):,}', 'stub', False
        if k == 'part':          # what's in it: unread ones stand out, as a folder's do
            return (f'{d["total"]:,}', 'git_dirty', True) if d['unread'] == d['total'] else \
                (f'{d["total"]:,}', 'git_staged' if d['flagged'] else 'stub', d['flagged'])
        if k == 'thread' and d['count'] > 1:
            return str(d['count']), 'stub', False
        if k == 'person' and d['address']:
            return f'{d["role"]}  {d["address"]}', 'stub', False
        if k == 'attachment':
            return f'{d["size"] / 1024:,.0f} KB', 'stub', False
        return None

    def unread(self, node):
        d = node.data
        return bool(d and node.kind in ('folder', 'thread', 'message') and d['unread'])

    def colour(self, node):
        k = node.kind
        if k == 'subject':
            return 'path', True
        if k in ('when', 'more'):
            return 'stub', False
        if k in ('group', 'part') and node.data['unread']:
            return 'dir', True
        return ('dir', True) if self.unread(node) else None

    def chip(self, node):
        """People in their colours, as in chats: their own, from their address."""
        if node.kind == 'person' and node.data['address']:
            if node.data['address'].lower() in {a.address.lower() for a in self.accounts}:
                return 'home_fg', 'home_bg'                     # you
            return 'person_fg', f'person{zlib.crc32(node.data["address"].lower().encode()) % fc.PEOPLE}'
        return None

    def width(self, node):
        return BODY_WIDTH if node.kind == 'body' else None

    def mark(self, node):
        if node.kind in ('thread', 'message'):
            if node.data['unread']:
                return 'git_dirty', '●', '*'
            if node.data['flagged']:
                return 'git_staged', '★', '!'
        if node.kind == 'part' and node.data['flagged']:
            return 'git_staged', '★', '!'
        if node.kind in ('group', 'part') and node.data['unread']:
            return 'git_dirty', '●', '*'
        return None

    def mark_before(self, node):
        return node.kind in ('part', 'group', 'thread', 'message')     # unread, before the subject, as tuido's states

    def dim(self, node):
        return 0.8 if node.kind == 'folder' and not node.data['total'] else 1.0

    def pane_for(self, node):
        """The message is on the canvas: the pane only shows its pictures."""
        path = self.message_path(node)
        return bool(path and pictures(path))

    def bar(self, node, plain):
        names, n = [], node     # names, not labels: no counts or dates
        while n.parent:
            names.append(n.data['author'] if n.kind == 'message' else n.name.split('\n')[0][:40]); n = n.parent
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
        if node.kind in ('folder', 'part', 'group'):
            if self.split != 'none':
                tail.append((f'{self.split} apart', 'bg', 'filter', False))
            tail.append((f'by {self.grouping}' if self.grouping != 'all' else 'not grouped', 'bg', 'filter', False))
        return segs, tail

    # -- the pane: a message's pictures
    def preview(self, node):
        path = self.message_path(node)
        return pictures(path) or None if path else None

    def choose(self, node):
        """Enter on '↓ more' shows more of a long message; elsewhere a branch opens."""
        if node.kind == 'more':
            self.lines[node.data['path']] = self.lines.get(node.data['path'], BODY_LINES) + MORE_LINES
            self.invalidate(node.parent.path)
            return fc.STAY
        return None

    # -- writing, and what's attached
    def message_path(self, node):
        """The file of the message `node` is, or is part of (a thread of one is its message), or None."""
        while node is not None and node.kind in ('subject', 'person', 'when', 'body', 'more', 'attachment'):
            node = node.parent
        if node is None:
            return None
        if node.kind == 'message':
            return node.data['path']
        if node.kind == 'thread' and node.data['count'] == 1:
            return node.data['messages'][0]['path']
        return None

    def account_of(self, node):
        while node is not None and node.kind != 'account':
            node = node.parent
        return node.data if node is not None else None

    def actions(self, node):
        if node.kind in ('folder', 'part', 'group'):  # how the folder branches: split, then by date, sender, or not
            nxt = self.next_split()
            out = [('\x0e', 'new', '⌃N new'), ('\x0c', 'group', f'⌃L by {self.next_grouping()}'),
                   ('\x14', 'split', '⌃T not split' if nxt == 'none' else f'⌃T {nxt} apart')]
            return out
        if node.kind == 'attachment':
            return [('\x0f', 'open-one', '⌃O open'), ('\x13', 'save-one', '⌃S save'), ('\x12', 'reply', '⌃R reply')]
        if node.kind in ('drafts',):
            return [('\x0e', 'new', '⌃N new')]
        if node.kind == 'draft':
            return [('\x05', 'edit', '⌃E edit'), ('\x04', 'discard', '⌃D throw away'), ('\x0e', 'new', '⌃N new')]
        out = [('\x0e', 'new', '⌃N new')]
        path = self.message_path(node)
        if path:
            out += [('\x12', 'reply', '⌃R reply'), ('\x01', 'reply-all', '⌃A reply all'),
                    ('\x06', 'forward', '⌃F forward')]
            if attachments(path):
                out += [('\x0f', 'open', '⌃O open attached'), ('\x13', 'save', '⌃S save attached')]
        return out if node.kind in ('account', 'thread', 'message', 'subject', 'person', 'when', 'body', 'more') else []

    def act(self, node, name, canvas):
        a = self.account_of(node)
        path = self.message_path(node)
        if name == 'new':
            self.write(canvas, a, compose.new(a))
        elif name in ('reply', 'reply-all'):
            self.write(canvas, a, compose.reply(a, path, everyone=name == 'reply-all',
                                                mine=[x.address for x in self.accounts]))
        elif name == 'forward':
            self.write(canvas, a, compose.forward(a, path))
        elif name == 'edit':
            self.write(canvas, a, node.data['path'])
        elif name == 'discard':
            def done(k):
                if k == 'y':
                    compose.discard(node.data['path'])
                    self.invalidate(node.parent.path)
                    self.invalidate(node.parent.parent.path)
                    canvas.say('thrown away')
            canvas.ask('throw this draft away?  y yes  n no', done, choices='yn')
        elif name == 'group':
            self.grouping = self.next_grouping()
            self.keep()
            self.invalidate()
            canvas.status('group', {'all': 'not grouped'}.get(self.grouping, f'grouped by {self.grouping}'), 'note')
        elif name == 'split':
            self.split = self.next_split()
            self.keep()
            self.invalidate()
            canvas.status('group', {'none': 'not split', 'unread': 'unread apart', 'flagged': 'flagged apart'}[self.split],
                          'note')
        elif name in ('open-one', 'save-one'):
            f = extract(node.data['message'])[node.data['index']]
            if name == 'open-one':
                files.open_file(f)
                canvas.say(f'opening {os.path.basename(f)}')
            else:
                canvas.say(f'saved to {files.copy_to(f, files.downloads())}')
        elif name == 'open':
            self.attached(canvas, path, lambda f: (files.open_file(f), canvas.say(f'opening {os.path.basename(f)}')))
        elif name == 'save':
            saved = [files.copy_to(f, files.downloads()) for f in extract(path)]
            canvas.say(f'saved {len(saved)} to {files.downloads()}' if saved else 'nothing attached')
        return None

    def next_split(self):
        ways = list(self.SPLITS)
        return ways[(ways.index(self.split) + 1) % len(ways)]

    def next_grouping(self):
        return self.GROUPINGS[(self.GROUPINGS.index(self.grouping) + 1) % len(self.GROUPINGS)]

    def write(self, canvas, a, path):
        """Edit the draft in your editor, outside the canvas, then ask what to do with it."""
        def edit():
            fc.terminal(compose.edit_command(path))
            self.after_edit(canvas, a, path)
        canvas.outside(edit)

    def after_edit(self, canvas, a, path):
        headers, attach, body = compose.parse(path)
        to = headers.get('To') or headers.get('Cc') or headers.get('Bcc') or 'nobody'
        extra = f', {len(attach)} attached' if attach else ''
        account_node = '/' + a.key

        def done(k):
            if k == 'y':
                try:
                    canvas.say(f'sent to {compose.send(path, a)}')
                except (OSError, ValueError, subprocess.SubprocessError) as e:
                    canvas.say(f'not sent ({e.strerror if isinstance(e, OSError) and e.strerror else e}): kept in Unsent')
            elif k == 'e':
                self.write(canvas, a, path)
            elif k == 'a':
                def choose():
                    chosen = self.pick(files.downloads())
                    if chosen:
                        compose.attach(path, chosen)
                    self.after_edit(canvas, a, path)
                canvas.outside(choose)
            elif k == 'd':
                canvas.say('kept in Unsent')
            elif k == 'q':
                compose.discard(path)
                canvas.say('thrown away')
            self.invalidate(account_node)
            self.invalidate(account_node + SEP + 'drafts')
        canvas.ask(f'to {fc.clip(to, 40)}{extra}:  y send  e edit  a attach  d keep for later  q throw away',
                   done, choices='yeadq')

    def attached(self, canvas, path, then):
        """then(file) for one of the message's attachments: the only one, or the one you pick."""
        found = extract(path)
        if len(found) == 1:
            then(found[0])
        elif found:
            shown = found[:9]
            names = '  '.join(f'{i + 1} {fc.clip(os.path.basename(f), 24)}' for i, f in enumerate(shown))
            canvas.ask(f'open which?  {names}', lambda k: then(shown[int(k) - 1]),
                       choices=''.join(str(i + 1) for i in range(len(shown))))
        else:
            canvas.say('nothing attached')


def attachments(path):
    """The names of what's attached to the message in `path`."""
    try:
        with open(path, 'rb') as f:
            msg = email.message_from_binary_file(f, policy=email.policy.default)
    except OSError:
        return []
    return [a.get_filename() or a.get_content_type().replace('/', '.') for a in msg.iter_attachments()]


def pictures(path):
    """The images attached to the message in `path`, for the pane: fc.Pictures of cached files."""
    try:
        with open(path, 'rb') as f:
            msg = email.message_from_binary_file(f, policy=email.policy.default)
    except OSError:
        return []
    if not any(a.get_content_maintype() == 'image' for a in msg.iter_attachments()):
        return []
    kinds = [a.get_content_maintype() for a in msg.iter_attachments()]
    return [fc.Picture(f) for f, kind in zip(extract(path), kinds) if kind == 'image']


def extract(path):
    """What's attached to the message in `path`, as files in msgcanvas's cache (made once)."""
    with open(path, 'rb') as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    folder = os.path.join(files.CACHE, 'attachments', hashlib.sha1(path.encode()).hexdigest()[:16])
    out = []
    for a in msg.iter_attachments():
        name = files.safe_name(a.get_filename() or a.get_content_type().replace('/', '.'))
        target = os.path.join(folder, name)
        if not os.path.exists(target):
            data = a.get_payload(decode=True)
            if data is None and a.is_multipart():          # an attached message
                data = a.as_bytes()
            os.makedirs(folder, exist_ok=True)
            with open(target, 'wb') as f:
                f.write(data or b'')
        out.append(target)
    return out
