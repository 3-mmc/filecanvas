"""Thunderbird's mail as the native Emails are drawn: accounts, their folders in the same order,
a folder's threads by date or sender, and a message's subject, people, date, body and files.
Lazy: RPC work never blocks canvas drawing."""
import datetime
import email.utils
import hashlib
import queue
import threading
import time
import zlib
from twigs import core as fc
from twigs.kit.dates import when_group
from twigs.kit.text import plain
from twigs.apps.msg.common import BODY_LINES, BODY_WIDTH, MORE_LINES, folder_name, folder_rank, when, who
from twigs.apps.msg.thunderbird import short_name
from .connection import Client
from . import settings

PAGE = 200            # messages asked for at a time; threads and groups are made from what's loaded
GROUPINGS = ('date', 'sender', 'all')
BEACON_DAYS = 30
# Thunderbird's folder types, ranked as ORDER ranks the native folders by name
RANKS = dict(inbox=0, sent=1, drafts=2, archive=3, junk=5, trash=6)
CONTENT = ('tb-subject', 'tb-person', 'tb-when', 'tb-body', 'tb-lines', 'tb-attachment')


def key(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:24]


def stamp(date):
    """Thunderbird's ISO date as seconds; 0 when it has none."""
    try:
        return datetime.datetime.fromisoformat(str(date).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0


def folders(rows):
    """Thunderbird's folder rows (each with its depth) as the native list: every folder by its
    path from the account, Gmail's [Gmail] container dropped, in the native order."""
    out, stack = [], []
    for row in rows:
        depth = max(0, int(row.get('depth', 0)))
        del stack[depth:]
        stack.append(row['name'])
        if row['name'] == '[Gmail]' and depth == 0:
            continue
        rel = '/'.join(stack)
        rank = RANKS.get(row.get('type'), folder_rank(rel)[0])
        out.append(dict(row, rel=rel, rank=rank))
    return sorted(out, key=lambda f: (f['rank'], folder_name(f['rel']).casefold()))


def threads(messages):
    """Messages as threads, newest first: each oldest message first, and whether any is unread."""
    found = {}
    for m in messages:
        found.setdefault((m['folderPath'], m.get('threadId') or m['id']), []).append(dict(m, stamp=stamp(m.get('date'))))
    out = []
    for tid, msgs in found.items():
        msgs.sort(key=lambda m: m['stamp'])
        out.append(dict(tid=tid, subject=msgs[0].get('subject') or '(no subject)', messages=msgs,
                        date=msgs[-1]['stamp'], count=len(msgs), author=who(msgs[0].get('author')),
                        unread=any(m.get('read') is False for m in msgs),
                        flagged=any(m.get('flagged') for m in msgs)))
    return sorted(out, key=lambda t: -t['date'])


def groups(found, grouping, now=None):
    """Threads in groups, the newest group first: by when each thread's newest came, or who began it."""
    out = {}
    for t in found:
        out.setdefault(when_group(t['date'], now) if grouping == 'date' else t['author'], []).append(t)
    return sorted(out.items(), key=lambda g: -g[1][0]['date'])


class Thunderbird(fc.Provider):
    def __init__(self, config, client_factory=Client, grouping='date'):
        self.config, self.client_factory = config, client_factory
        self.grouping = grouping
        self.client = None
        self.snapshots, self.errors, self.pending, self.queries, self.versions = {}, {}, {}, {}, {}
        self.lines = {}                         # a message's id: how many lines of its body show
        self.mine = set()                       # the selected accounts' addresses, for "you"
        self.lock = threading.Lock()
        self.jobs = queue.Queue()
        self.closed = False
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def root(self):
        return fc.Node('/thunderbird', 'Emails', True, source=self, kind='tb-root')

    @staticmethod
    def message_of(node):
        """The message `node` reads: its own, or a thread of one's."""
        if node.kind == 'tb-message':
            return node.data
        if node.kind == 'tb-thread' and node.data['count'] == 1:
            return node.data['messages'][0]
        return None

    def request(self, node, offset=0, force=False):
        with self.lock:
            if self.closed or (node.path in self.pending and not force):
                return
            version = self.versions.get(node.path, 0) + 1
            self.versions[node.path] = self.pending[node.path] = version
            query = self.queries.get(node.path, '')
        self.jobs.put((node, offset, query, version))

    def run(self):
        while True:
            job = self.jobs.get()
            if job is None or self.closed:
                return
            node, offset, query, version = job
            with self.lock:
                if self.pending.get(node.path) != version:
                    continue
            try:
                if self.client is None or self.client.closed:
                    self.client = self.client_factory(self.config)
                if node.kind == 'tb-root':
                    accounts = self.client.call('listAccounts')
                    selected, _ = settings.selection(accounts, self.config)
                    data = [a for a in accounts if a['id'] in selected and a['id'] in self.config['accounts']]
                    self.mine = {i['email'].casefold() for a in data for i in a.get('identities', []) if i.get('email')}
                elif node.kind == 'tb-account':
                    data = folders(self.client.call('listFolders', dict(accountId=node.data['id'], format='objects')))
                elif node.kind == 'tb-folder':
                    data = self.client.call('searchMessages', dict(query=query, folderPath=node.data['path'],
                        includeSubfolders=False, maxResults=PAGE, offset=offset, sortOrder='desc', fullRecipients=True))
                    if not isinstance(data, dict) or not isinstance(data.get('messages'), list):
                        raise OSError('Thunderbird did not return a paginated message list; update the extension')
                    if offset:
                        with self.lock:
                            previous = self.snapshots.get(node.path, {}).get('messages', [])
                        seen = {(m['folderPath'], m['id']) for m in previous}
                        data = dict(data, messages=previous + [m for m in data['messages'] if (m['folderPath'], m['id']) not in seen])
                elif self.message_of(node) is not None:
                    m = self.message_of(node)
                    data = self.client.call('getMessage', dict(messageId=m['id'], folderPath=m['folderPath'],
                        bodyFormat='text', saveAttachments=False, includeInlineImages=False))
                    if not isinstance(data, dict) or data.get('bodyIsHtml'):
                        raise OSError('Thunderbird returned an unsupported message format')
                else:
                    continue
                with self.lock:
                    if self.pending.get(node.path) == version:
                        self.snapshots[node.path] = data
                        self.errors.pop(node.path, None)
            except (OSError, ValueError, KeyError, TypeError) as e:
                with self.lock:
                    if self.pending.get(node.path) == version:
                        self.errors[node.path] = self.explain(e)
                if self.client:
                    self.client.close()
            finally:
                with self.lock:
                    if self.pending.get(node.path) == version:
                        self.pending.pop(node.path, None)
                if not self.closed:
                    self.invalidate(node.path, deep=True)

    @staticmethod
    def explain(error):
        """What to do about a failed request: Thunderbird stopped or its extension off say so."""
        try:
            from . import host
            hint = host.hint()
        except (OSError, ValueError):
            hint = None
        return hint or str(error)

    # -- the tree
    def children(self, node):
        k = node.kind
        if k == 'tb-group':
            return [self.thread_node(t, node) for t in node.data['threads']]
        if k == 'tb-thread' and node.data['count'] > 1:
            return [self.message_node(m, node) for m in node.data['messages']]
        if k not in ('tb-root', 'tb-account', 'tb-folder') and self.message_of(node) is None:
            return []
        with self.lock:
            data, error = self.snapshots.get(node.path), self.errors.get(node.path)
        if data is None and not error:
            self.request(node)
        out = []

        def add(suffix, label, branch=False, kind='tb-note', data=None):
            out.append(fc.Node(node.path+'/'+suffix, plain(label), branch, node, kind, data=data))
        if error:
            add('error', error+'\nCtrl+R to retry')
        if data is None:
            if not error:
                add('loading', 'Loading Thunderbird…')
            return out
        if k == 'tb-root':
            names = set()
            for a in data:
                address = next((i['email'] for i in a.get('identities', []) if i.get('email')), '')
                name = short_name(address) if address and a['name'] == address else a['name']
                while name in names:
                    name += '-2'
                names.add(name)
                add('a'+key(a['id']), name, True, 'tb-account', a)
        elif k == 'tb-account':
            for f in data:
                add('f'+key(f['path']), folder_name(f['rel']), True, 'tb-folder', f)
        elif k == 'tb-folder':
            query = self.queries.get(node.path, '')
            if query:
                add('query', 'Search: '+query+'\nCtrl+F to change or clear')
            found = threads(data['messages'])
            if self.grouping == 'all':
                out += [self.thread_node(t, node) for t in found]
            else:
                for name, ts in groups(found, self.grouping):
                    add(self.grouping+':'+key(name), name, True, 'tb-group',
                        dict(threads=ts, unread=sum(t['unread'] for t in ts)))
            if data.get('truncated'):
                add('partial', data.get('message') or 'Partial results; narrow the search or folder')
            refresh = data.get('folderRefresh', {})
            if refresh and not refresh.get('updateConfirmed'):
                add('freshness', refresh.get('warning') or 'Showing available mail; server refresh is unconfirmed')
            if data.get('hasMore'):
                # Offset is the server's page position, independent of duplicate suppression.
                add('more', '↓ More messages', False, 'tb-more',
                    int(data.get('offset', 0))+int(data.get('limit', PAGE)))
        else:
            out += self.contents(node, data)
        if not out:
            add('empty', 'No results' if k == 'tb-folder' else 'No accounts or folders selected')
        return out

    def thread_node(self, t, parent):
        return fc.Node(parent.path+'/t'+key(t['tid']), plain(t['subject']), True, parent, 'tb-thread', data=t)

    def message_node(self, m, parent):
        return fc.Node(parent.path+'/m'+key(m['folderPath']+'\0'+m['id']),
                       plain(f"{who(m.get('author'))} · {when(m['stamp'])}"), True, parent, 'tb-message', data=m)

    def contents(self, node, data):
        """What a message says, as branches beside it, as the native Emails show it."""
        m, out = self.message_of(node), []

        def add(kind, suffix, label, extra=None):
            out.append(fc.Node(node.path+'/'+suffix, plain(label), False, node, kind, data=extra))
        add('tb-subject', 'subject', data.get('subject') or m.get('subject') or '(no subject)')
        people = lambda field: [(n, a) for n, a in email.utils.getaddresses([data.get(field) or '']) if a or n]
        to, cc = people('recipients'), people('ccList')
        everyone = [('from', p) for p in people('author')] + [('to', p) for p in to[:6]] + [('cc', p) for p in cc[:4]]
        for i, (role, (name, address)) in enumerate(everyone):
            add('tb-person', f'p{i}', name or address, dict(role=role, address=address))
        hidden = max(0, len(to) - 6) + max(0, len(cc) - 4)
        if hidden:
            add('tb-person', 'p+', f'and {hidden} more', dict(role='', address=''))
        sent = stamp(data.get('date') or m.get('date'))
        if sent:
            add('tb-when', 'when', time.strftime('%a %-d %b %Y, %H:%M', time.localtime(sent)))
        shown = self.lines.get(m['id'], BODY_LINES)
        lines, paragraph, n = (data.get('body') or '').replace('\r', '').strip().split('\n'), [], 0
        for line in lines[:shown] + ['']:
            if line.strip():
                paragraph.append(line.rstrip())
            elif paragraph:
                add('tb-body', f'b{n}', '\n'.join(paragraph))
                paragraph, n = [], n + 1
        if not n:
            add('tb-body', 'b0', '(no readable body)')
        if len(lines) > shown:
            add('tb-lines', 'lines', f'↓ {len(lines) - shown} more lines', dict(id=m['id']))
        if data.get('bodyNote'):
            add('tb-note', 'body-note', data['bodyNote'])
        for i, a in enumerate(data.get('attachments', [])):
            add('tb-attachment', f'a{i}', f"📎 {a.get('name') or 'Attachment'}", dict(size=a.get('size')))
        return out

    def choose(self, node):
        if node.kind == 'tb-more':
            self.request(node.parent, offset=node.data)
        elif node.kind == 'tb-lines':
            self.lines[node.data['id']] = self.lines.get(node.data['id'], BODY_LINES) + MORE_LINES
            self.invalidate(node.parent.path, deep=True)
        return None if node.is_dir else fc.STAY

    def actions(self, node):
        out = [('\x12', 'refresh', '⌃R refresh'), ('\x06', 'search', '⌃F search folder')]
        if node.kind in ('tb-folder', 'tb-group'):
            nxt = GROUPINGS[(GROUPINGS.index(self.grouping) + 1) % len(GROUPINGS)]
            out.append(('\x0c', 'group', f'⌃L by {nxt}' if nxt != 'all' else '⌃L not grouped'))
        return out

    def act(self, node, name, canvas):
        if name == 'search':
            while node.parent and node.kind != 'tb-folder':
                node = node.parent
            if node.kind != 'tb-folder':
                canvas.say('Open a folder to search its messages')
                return fc.STAY
            def search(text):
                with self.lock:
                    self.queries[node.path] = text.strip()
                    self.snapshots.pop(node.path, None)
                    self.errors.pop(node.path, None)
                self.request(node, force=True)
                self.invalidate(node.path, deep=True)
            canvas.ask('Search this folder (empty clears)', search, text=self.queries.get(node.path, ''))
        elif name == 'group':
            self.grouping = GROUPINGS[(GROUPINGS.index(self.grouping) + 1) % len(GROUPINGS)]
            while node.kind != 'tb-folder':
                node = node.parent
            self.invalidate(node.path, deep=True)
            canvas.say(f'by {self.grouping}' if self.grouping != 'all' else 'not grouped')
        elif name == 'refresh':
            while node.parent and node.kind not in ('tb-root', 'tb-account', 'tb-folder') and self.message_of(node) is None:
                node = node.parent
            self.request(node, force=True)
            canvas.say('Refreshing from Thunderbird')
        return fc.STAY

    # -- how it looks: as the native Emails
    def count(self, node):
        k, d = node.kind, node.data
        if k == 'tb-folder':
            return (f"{d['unreadMessages']:,}", 'git_dirty', True) if d.get('unreadMessages') else None
        if k == 'tb-group':
            return f"{len(d['threads']):,}", 'stub', False
        if k == 'tb-thread' and d['count'] > 1:
            return str(d['count']), 'stub', False
        if k == 'tb-person' and d['address']:
            return f"{d['role']}  {d['address']}", 'stub', False
        if k == 'tb-attachment' and isinstance(d.get('size'), (int, float)):
            return f"{d['size'] / 1024:,.0f} KB", 'stub', False
        return None

    def unread(self, node):
        k, d = node.kind, node.data
        if k == 'tb-folder':
            return bool(d.get('unreadMessages'))
        if k in ('tb-thread', 'tb-group'):
            return bool(d['unread'])
        return k == 'tb-message' and d.get('read') is False

    def colour(self, node):
        if node.kind == 'tb-subject':
            return 'path', True
        if node.kind in ('tb-when', 'tb-lines'):
            return 'stub', False
        return ('dir', True) if self.unread(node) else None

    def chip(self, node):
        """People in their colours, as in the native Emails and chats: their own, from their address."""
        if node.kind == 'tb-person' and node.data['address']:
            address = node.data['address'].casefold()
            if address in self.mine:
                return 'home_fg', 'home_bg'                     # you
            return 'person_fg', f'person{zlib.crc32(address.encode()) % fc.PEOPLE}'
        return None

    def mark(self, node):
        k, d = node.kind, node.data
        if k in ('tb-thread', 'tb-message', 'tb-group'):
            if self.unread(node):
                return 'git_dirty', '●', '*'
            if d.get('flagged'):
                return 'git_staged', '★', '!'
        return None

    def mark_before(self, node):
        return node.kind in ('tb-group', 'tb-thread', 'tb-message')

    def beacon(self, node):
        """Unread mail newer than BEACON_DAYS as a beacon, as the native Emails show it."""
        if node.kind == 'tb-thread' and node.data['unread']:
            came = max(m['stamp'] for m in node.data['messages'] if m.get('read') is False)
        elif node.kind == 'tb-message' and node.data.get('read') is False:
            came = node.data['stamp']
        else:
            return None
        return came if came and time.time() - came < BEACON_DAYS * 86400 else None

    def dim(self, node):
        return 0.8 if node.kind == 'tb-folder' and not node.data.get('totalMessages') else 1.0

    def width(self, node):
        return BODY_WIDTH if node.kind in ('tb-body', 'tb-note') else None

    def reference(self, node):
        at = node
        while at.parent and at.kind not in ('tb-message', 'tb-thread'):
            at = at.parent
        if at.kind not in ('tb-message', 'tb-thread'):
            at = node
        content = [n for n in at.load() if n.kind in CONTENT]
        return dict(root=at, nodes=content, priority=0, width=BODY_WIDTH, labels={})

    def bar(self, node, plain):
        names, n = [], node
        while n.parent:
            names.append(who(n.data.get('author')) if n.kind == 'tb-message' else n.name.split('\n')[0][:40])
            n = n.parent
        names.reverse()
        segs = [('mail', 'home_fg', 'home_bg', False)] + [
            (t, 'cwd_fg' if i == len(names) - 1 else 'seg_fg', 'seg_bg', i == len(names) - 1)
            for i, t in enumerate(names)]
        tail = []
        f = node
        while f is not None and f.kind != 'tb-folder':
            f = f.parent
        if f is not None:
            d = f.data
            tail.append((f"{d['unreadMessages']} unread" if d.get('unreadMessages') else f"{d.get('totalMessages', 0)} messages",
                         'repo_dirty_fg' if d.get('unreadMessages') else 'repo_clean_fg',
                         'repo_dirty_bg' if d.get('unreadMessages') else 'repo_clean_bg', False))
        if node.kind in ('tb-folder', 'tb-group'):
            tail.append((f'by {self.grouping}' if self.grouping != 'all' else 'not grouped', 'bg', 'filter', False))
        return segs, tail + [('via Thunderbird', 'seg_fg', 'seg_bg', False)]

    def busy(self):
        with self.lock:
            return bool(self.pending)

    def close(self):
        self.closed = True
        self.jobs.put(None)
        if self.client:
            self.client.close()
        self.worker.join(timeout=3)


class NotConnected(fc.Provider):
    """Emails, before twigs thunderbird connect: what to run."""
    def root(self):
        return fc.Node('/thunderbird', 'Emails', True, source=self, kind='tb-root')

    def children(self, node):
        if node.kind != 'tb-root':
            return []
        return [fc.Node(node.path+'/connect', 'Thunderbird is ready, but no accounts are connected yet\n'
                        'Run: twigs thunderbird connect', False, node, 'tb-note')]

    def width(self, node):
        return BODY_WIDTH
