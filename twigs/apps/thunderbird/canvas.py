"""Lazy Thunderbird folders and mail; RPC work never blocks canvas drawing."""
import hashlib
import queue
import threading
from twigs import core as fc
from twigs.kit.text import plain
from .connection import Client
from . import settings


def key(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:24]


def folder_tree(rows):
    roots, stack = [], []
    for row in rows:
        item = dict(row, children=[])
        depth = max(0, int(row.get('depth', 0)))
        while stack and stack[-1][0] >= depth:
            stack.pop()
        (stack[-1][1]['children'] if stack else roots).append(item)
        stack.append((depth, item))
    return roots


class Thunderbird(fc.Provider):
    def __init__(self, config, client_factory=Client):
        self.config, self.client_factory = config, client_factory
        self.client = None
        self.snapshots, self.errors, self.pending, self.queries, self.versions = {}, {}, {}, {}, {}
        self.lock = threading.Lock()
        self.jobs = queue.Queue()
        self.closed = False
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def root(self):
        return fc.Node('/thunderbird', 'Thunderbird', True, source=self, kind='tb-root')

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
                    selected, _ = settings.choose(accounts, self.config['accounts'],
                        self.config.get('include_existing', False))
                    data = [a for a in accounts if a['id'] in selected and a['id'] in self.config['accounts']]
                elif node.kind == 'tb-account':
                    data = folder_tree(self.client.call('listFolders', dict(accountId=node.data['id'], format='objects')))
                elif node.kind == 'tb-folder':
                    data = self.client.call('searchMessages', dict(query=query, folderPath=node.data['path'],
                        includeSubfolders=False, maxResults=50, offset=offset, sortOrder='desc', fullRecipients=True))
                    if not isinstance(data, dict) or not isinstance(data.get('messages'), list):
                        raise OSError('Thunderbird did not return a paginated message list; update the extension')
                    if offset:
                        with self.lock:
                            previous = self.snapshots.get(node.path, {}).get('messages', [])
                        seen = {(m['folderPath'], m['id']) for m in previous}
                        data = dict(data, messages=previous + [m for m in data['messages'] if (m['folderPath'], m['id']) not in seen])
                elif node.kind == 'tb-message':
                    data = self.client.call('getMessage', dict(messageId=node.data['id'], folderPath=node.data['folderPath'],
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
                        self.errors[node.path] = str(e)
                if self.client:
                    self.client.close()
            finally:
                with self.lock:
                    if self.pending.get(node.path) == version:
                        self.pending.pop(node.path, None)
                if not self.closed:
                    self.invalidate(node.path, deep=True)

    def children(self, node):
        if node.kind not in ('tb-root', 'tb-account', 'tb-folder', 'tb-message'):
            return []
        with self.lock:
            data, error = self.snapshots.get(node.path), self.errors.get(node.path)
        if data is None and not error:
            self.request(node)
        out = []
        def add(suffix, label, branch=False, kind='tb-note', data=None):
            n = fc.Node(node.path+'/'+suffix, plain(label), branch, node, kind, data=data)
            out.append(n)
            return n
        if error:
            add('error', error+'\nCtrl+R to retry')
        if data is None:
            if not error:
                add('loading', 'Loading Thunderbird…')
            return out
        def folder(f):
            add('f'+key(f['path']), f['name'], True, 'tb-folder', f)
        if node.kind == 'tb-root':
            for a in data:
                add('a'+key(a['id']), a['name'], True, 'tb-account', a)
        elif node.kind == 'tb-account':
            for f in data:
                folder(f)
        elif node.kind == 'tb-folder':
            for f in node.data.get('children', []):
                folder(f)
            query = self.queries.get(node.path, '')
            if query:
                add('query', 'Search: '+query+'\nCtrl+F to change or clear')
            for m in data['messages']:
                add('m'+key(m['folderPath']+'\0'+m['id']),
                    (m.get('subject') or '(no subject)')+'\n'+m.get('author', ''), True, 'tb-message', m)
            if data.get('truncated'):
                add('partial', data.get('message') or 'Partial results; narrow the search or folder')
            refresh = data.get('folderRefresh', {})
            if refresh and not refresh.get('updateConfirmed'):
                add('freshness', refresh.get('warning') or 'Showing available mail; server refresh is unconfirmed')
            if data.get('hasMore'):
                # Offset is the server's page position, independent of duplicate suppression.
                add('more', '↓ More messages', False, 'tb-more',
                    int(data.get('offset', 0))+int(data.get('limit', 50)))
        elif node.kind == 'tb-message':
            add('subject', data.get('subject') or node.data.get('subject') or '(no subject)')
            add('from', data.get('author', '')+'\n'+str(data.get('date') or ''), kind='tb-sender')
            add('to', 'To: '+data.get('recipients', ''))
            if data.get('ccList'):
                add('cc', 'Cc: '+data['ccList'])
            add('body', data.get('body') or '(no readable body)', kind='tb-body')
            if data.get('bodyNote'):
                add('body-note', data['bodyNote'])
            for i, a in enumerate(data.get('attachments', [])):
                add('attachment'+str(i), f"{a.get('name') or 'Attachment'} ({a.get('size', '?')} bytes)", kind='tb-attachment')
        if not out:
            add('empty', 'No results' if node.kind == 'tb-folder' else 'No accounts or folders selected')
        return out

    def choose(self, node):
        if node.kind == 'tb-more':
            self.request(node.parent, offset=node.data)
        return None if node.is_dir else fc.STAY

    def actions(self, node):
        return [('\x12', 'refresh', '⌃R refresh'), ('\x06', 'search', '⌃F search folder')]

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
        elif name == 'refresh':
            while node.parent and node.kind not in ('tb-root', 'tb-account', 'tb-folder', 'tb-message'):
                node = node.parent
            self.request(node, force=True)
            canvas.say('Refreshing from Thunderbird')
        return fc.STAY

    def count(self, node):
        if node.kind == 'tb-folder' and node.data.get('unreadMessages', 0) > 0:
            return str(node.data['unreadMessages']), 'filter', True

    def mark(self, node):
        if node.kind == 'tb-message' and node.data.get('read') is False:
            return 'filter', '●', '*'

    def width(self, node):
        return 66 if node.kind in ('tb-body', 'tb-note', 'tb-sender', 'tb-attachment') else 42 if node.kind == 'tb-message' else None

    def chip(self, node):
        return ('cwd_fg', 'cwd_bg') if node.kind == 'tb-sender' else None

    def chip_rows(self, node):
        return 1 if node.kind == 'tb-sender' else None

    def reference(self, node):
        at = node
        while at.parent and at.kind != 'tb-message':
            at = at.parent
        return dict(root=at, nodes=at.load() if at.kind == 'tb-message' else [], priority=0, width=66, labels={})

    def busy(self):
        with self.lock:
            return bool(self.pending)

    def close(self):
        self.closed = True
        self.jobs.put(None)
        if self.client:
            self.client.close()
        self.worker.join(timeout=3)
