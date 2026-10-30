"""Chats on the canvas: nchat's profiles (WhatsApp, Signal, Instagram, …), their chats (pinned,
then the latest first), and each chat's messages; the reading pane shows the conversation, newest
at the bottom. ⌃N writes in a chat, ⌃R replies to a message, ⌃A sends a file, ⌃O / ⌃S open or
save a message's file (fetching it first).

nchat runs as a child, `nchat --serve`, speaking JSON lines (nchat's doc/serve.md): requests go
in, replies and events come out. Everything it says is kept here, by profile, chat and message
id, and the canvas is invalidated where it changed. Nothing is marked read yet."""
import json, os, subprocess, threading, time

from . import files, filecanvas as fc
from .mail import SEP, when

PROTOCOLS = {'WhatsAppMd': 'WhatsApp', 'Signal': 'Signal', 'Instagram': 'Instagram',
             'Telegram': 'Telegram', 'Dummy': 'Dummy'}
HISTORY = 50          # messages fetched when a chat is first looked at


def confdir():
    """nchat's own default: ~/.nchat if it's there, else $XDG_CONFIG_HOME/nchat."""
    legacy = os.path.expanduser('~/.nchat')
    if os.path.isdir(legacy):
        return legacy
    return os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'nchat')


def has_profiles(conf):
    try:
        return any(d != 'version' and '_' in d for d in os.listdir(os.path.join(conf, 'profiles')))
    except OSError:
        return False


class Profile:
    def __init__(self, info):
        self.id = info['profile']
        self.protocol = info.get('protocol', self.id.split('_')[0])
        self.name, self.self_id = info.get('name', ''), info.get('self', '')
        self.features = set(info.get('features', ()))
        self.chats, self.contacts, self.messages = {}, {}, {}   # by id; messages: {chat: {id: msg}}
        self.asked = set()          # chats whose history was asked for
        self.asked_chats = False    # the chat list, likewise
        self.typing = {}            # chat: {user}
        self.connected = None       # None until nchat says
        self.needs_setup = False
        self.unsent = {}            # chat: why the last message to it wasn't sent
        self.wanted = {}            # (chat, message): what to do with its file once it's downloaded
        self.fetching = set()       # (chat, message): pictures asked for, to show

    def who(self, user):
        if user == self.self_id or user == 'You':
            return 'You'
        return self.contacts.get(user, {}).get('name') or plain(user)

    def chat_name(self, chat):
        c = self.chats.get(chat, {})
        name = c.get('name') or chat
        return self.who(chat) if name == chat else name   # named by its id: a contact's name, once known


def plain(user):
    """Someone nchat has no name for, as a person would write them: WhatsApp's 4917…@s.whatsapp.net
    is +4917…, an unnamed group just 'group'."""
    local, at, server = (user or '').partition('@')
    if at and server == 'g.us':
        return 'group'
    if at and local.isdigit():
        return '+' + local
    return user or '(unknown)'


class Serve:
    """`nchat --serve` as a child process: request() writes, a thread reads, `on_change(path)`
    is called (from that thread) with the id of each canvas node whose contents changed."""

    def __init__(self, on_change, nchat='nchat', conf=None, log=None):
        self.on_change, self.lock = on_change, threading.Lock()
        self.profiles, self.skipped, self.error, self.next_id = {}, [], None, 0
        self.ready = threading.Event()
        self.then = []              # what an event set off, to do once the lock is let go
        cmd = [nchat, '--serve'] + (['-d', conf] if conf else [])
        err = open(log, 'a') if log else subprocess.DEVNULL     # nchat's fatal start-up errors
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                         text=True, encoding='utf-8', bufsize=1)
        except OSError as e:
            self.proc, self.error = None, f'nchat: {e.strerror or e}'
            self.ready.set()
            return
        finally:
            if log:
                err.close()      # the child has its own
        threading.Thread(target=self.read, daemon=True).start()

    def request(self, op, **fields):
        if not self.proc or self.error:
            return None
        with self.lock:
            self.next_id += 1
            n = self.next_id
            try:
                self.proc.stdin.write(json.dumps(dict(id=n, op=op, **fields)) + '\n')
                self.proc.stdin.flush()
            except (OSError, ValueError):
                self.error = 'nchat stopped'
                return None
        return n

    def close(self):
        """End nchat as serve.md says (EOF on its stdin: it logs out and lets go of its locks)."""
        if not self.proc:
            return
        try:
            self.proc.stdin.close()
            self.proc.wait(5)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()

    def read(self):
        for line in self.proc.stdout:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if 'event' in e:
                with self.lock:
                    changed = self.apply(e)
                    then, self.then = self.then, []
                for path in changed:
                    self.on_change(path)
                for fn in then:
                    try:
                        fn()
                    except Exception:
                        pass
            # replies: `ok` only means nchat took the request; what came of it arrives as events
        code = self.proc.wait()
        self.error = self.error or f'nchat stopped (exit {code})'
        self.ready.set()
        self.on_change(None)

    # -- what the events say
    def apply(self, e):
        """Take one event in; the ids of the canvas nodes it changed."""
        kind = e['event']
        if kind == 'hello':
            self.profiles = {p['profile']: Profile(p) for p in e.get('profiles', ())}
            self.skipped = e.get('skipped', [])
            self.ready.set()
            return [None]
        p = self.profiles.get(e.get('profile'))
        if p is None:
            return []
        top = profile_id(p.id)
        if kind == 'connected':
            p.connected = e.get('ok', False)
            return [None]
        if kind == 'needs_setup':
            p.needs_setup = True
            return [None]
        if kind == 'contacts' or kind == 'members':
            for c in e.get('contacts', e.get('members', ())):
                p.contacts[c['id']] = c
            return [top]
        if kind == 'chats':
            for c in e.get('chats', ()):
                p.chats[c['id']] = c
            return [top]
        chat = e.get('chat')
        node = chat_id(p.id, chat)
        msgs = p.messages.setdefault(chat, {}) if chat is not None else {}
        if kind == 'sent':
            if e.get('ok', True):
                p.unsent.pop(chat, None)
            else:
                p.unsent[chat] = 'not sent'
        if kind in ('messages', 'sent'):
            got = e.get('messages', ()) if kind == 'messages' else [e['message']] if e.get('message') else []
            for m in got:
                if m.get('id'):
                    msgs[m['id']] = m
            if got and chat in p.chats:
                c = p.chats[chat]
                c['last_message_time'] = max(c.get('last_message_time', -1), *(m.get('time', -1) for m in got))
            return [node, top]
        if kind == 'message_status' and e.get('message') in msgs:
            msgs[e['message']]['read'] = e.get('read', True)
            return [node]
        if kind == 'message_file' and e.get('message') in msgs:
            f = msgs[e['message']]['file'] = e.get('file')
            want = p.wanted.get((chat, e['message']))
            if want and f and f.get('status') in (1, 3):
                del p.wanted[(chat, e['message'])]
                if f.get('status') == 1 and f.get('path'):
                    self.then.append(lambda: want(f['path']))
            return [node]
        if kind == 'reactions' and e.get('message') in msgs:
            msgs[e['message']]['reactions'] = e.get('reactions')
            return [node]
        if kind == 'deleted' and e.get('ok', True):
            if 'message' in e:
                msgs.pop(e['message'], None)
                return [node]
            p.chats.pop(chat, None)
            return [top]
        if kind == 'typing':
            who = p.typing.setdefault(chat, set())
            (who.add if e.get('typing') else who.discard)(e.get('user'))
            return [node]
        if kind in ('pinned', 'muted', 'archived') and e.get('ok', True) and chat in p.chats:
            if 'message' in e and e['message'] in msgs:
                msgs[e['message']]['pinned'] = e.get('on')
                return [node]
            p.chats[chat][kind] = e.get('on')
            return [top]
        return []


def profile_id(profile):
    return '/@' + profile


def chat_id(profile, chat):
    return f'{profile_id(profile)}{SEP}{chat}'


class Chat(fc.Provider):
    """nchat's profiles → chats → messages. `KINDS` are the node kinds it answers for."""
    KINDS = ('profile', 'chat', 'chat-message')
    pane = True

    def __init__(self, nchat='nchat', conf=None, log=None):
        self.serve = Serve(lambda path: self.invalidate(path), nchat, conf, log)

    def root(self):
        return fc.Node('', 'chat', True, source=self, kind='root')

    # -- the tree
    def children(self, node):
        s = self.serve
        if node.kind == 'root':
            s.ready.wait(3)          # hello: which profiles there are
            if s.error and not s.profiles:
                raise OSError(0, s.error)
            out = [fc.Node(profile_id(p.id), PROTOCOLS.get(p.protocol, p.protocol), True, node, 'profile', data=p)
                   for p in s.profiles.values()]
            for k in s.skipped:
                out.append(fc.Node(profile_id(k['profile']), k['profile'], False, node, 'profile',
                                   data=dict(skipped=k.get('why', ''))))
            return out
        p = node.data
        if node.kind == 'profile':
            if isinstance(p, dict):
                raise OSError(0, p['skipped'])
            if p.needs_setup:
                raise OSError(0, 'needs signing in again: nchat --setup')
            with s.lock:
                ask, p.asked_chats = not p.asked_chats, True
                chats = sorted(p.chats.values(), key=lambda c: (bool(c.get('archived')), not c.get('pinned'),
                                                                -c.get('last_message_time', -1)))
            if ask:
                s.request('get_chats', profile=p.id)
            if not chats:
                raise OSError(0, 'connecting…' if p.connected is None else 'no chats yet')
            return [fc.Node(chat_id(p.id, c['id']), p.chat_name(c['id']), True, node, 'chat',
                            data=(p, c['id'])) for c in chats]
        if node.kind == 'chat':
            p, chat = node.data
            msgs = self.history(p, chat)
            return [fc.Node(f'{node.path}{SEP}{m["id"]}', self.line(p, m), False, node, 'chat-message',
                            data=(p, chat, m)) for m in reversed(msgs)]
        return []

    def history(self, p, chat):
        """The chat's messages, oldest first; the first look asks nchat for the latest."""
        with self.serve.lock:
            ask = chat not in p.asked
            p.asked.add(chat)
            msgs = sorted(p.messages.get(chat, {}).values(), key=lambda m: m.get('time', -1))
        if ask:
            self.serve.request('get_messages', profile=p.id, chat=chat, **{'from': ''}, limit=HISTORY)
        return msgs

    def line(self, p, m):
        text = (m.get('text') or '').split('\n')[0] or ('📎 ' + (m['file'] or {}).get('type', 'file')
                                                         if m.get('file') else '')
        return f'{p.who(m.get("sender"))}: {text}'

    def locate(self, start):
        return [], ''

    # -- how it looks
    def chat(self, node):
        p, chat = node.data[:2]
        return p.chats.get(chat, {})

    def label(self, node):
        if node.kind == 'profile' and isinstance(node.data, Profile):
            p = node.data
            who = p.name or p.id.split('_', 1)[-1]
            return f'{node.name} {who}' if who else node.name
        return node.name

    def unread(self, node):
        return node.kind == 'chat' and bool(self.chat(node).get('unread'))

    def colour(self, node):
        return ('dir', True) if self.unread(node) else None

    def mark(self, node):
        if node.kind == 'chat':
            c = self.chat(node)
            if c.get('unread'):
                return ('git_conflict', '@', '@') if c.get('unread_mention') else ('git_dirty', '●', '*')
            if c.get('pinned'):
                return 'git_staged', '★', '!'
        if node.kind == 'profile' and isinstance(node.data, Profile) and node.data.connected is False:
            return 'git_conflict', '✖', 'x'
        return None

    def dim(self, node):
        if node.kind == 'profile' and not isinstance(node.data, Profile):
            return 0.6                          # skipped: locked by a running nchat
        if node.kind == 'chat':
            c = self.chat(node)
            return 0.7 if c.get('archived') else 0.85 if c.get('muted') else 1.0
        return 1.0

    def bar(self, node, plain):
        names, n = [], node
        while n.parent:
            names.append(n.data[0].who(n.data[2].get('sender')) if n.kind == 'chat-message' else self.label(n))
            n = n.parent
        names.reverse()
        segs = [('chat', 'home_fg', 'home_bg', False)] + [
            (t, 'cwd_fg' if i == len(names) - 1 else 'seg_fg', 'seg_bg', i == len(names) - 1)
            for i, t in enumerate(names)]
        tail = []
        if node.kind in ('chat', 'chat-message'):
            p, chat = node.data[:2]
            typing = [p.who(u) for u in p.typing.get(chat, ())]
            if typing:
                tail.append((', '.join(typing) + ' typing…', 'repo_clean_fg', 'repo_clean_bg', False))
            if chat in p.unsent:
                tail.append(('⚠ ' + p.unsent[chat], 'repo_dirty_fg', 'repo_dirty_bg', False))
        return segs, tail

    # -- the reading pane
    def preview_end(self, node):
        return node.kind == 'chat'

    def preview(self, node):
        if node.kind == 'profile':
            p = node.data
            if not isinstance(p, Profile):
                return f'{node.name}: {p["skipped"]}' + (
                    '\n\nnchat is open on it: close nchat, then open this again.' if p['skipped'] == 'locked' else '')
            state = {None: 'connecting…', True: 'connected', False: 'not connected'}[p.connected]
            return f'{p.protocol} {p.name}\n{state}\n\n{len(p.chats)} chats'
        if node.kind == 'chat':
            p, chat = node.data
            msgs = self.history(p, chat)
            if not msgs:
                return '(fetching…)' if chat in p.asked else '(no messages)'
            out, last = [], None
            for m in msgs:
                who = p.who(m.get('sender'))
                day = when(m['time'] / 1000) if m.get('time', -1) > 0 else ''
                if (who, day) != last:          # a name, as a chip of the bar's, when it changes
                    out += ([''] if out else []) + [self.name_chip(who, f' {day}')]
                    last = (who, day)
                out.append('\n'.join('  ' + l for l in self.body(p, m).split('\n')))
            typing = [p.who(u) for u in p.typing.get(chat, ())]
            if typing:
                out += ['', ', '.join(typing) + ' typing…']
            return out
        if node.kind == 'chat-message':
            p, chat, m = node.data
            t = time.strftime('%Y-%m-%d %H:%M', time.localtime(m['time'] / 1000)) if m.get('time', -1) > 0 else ''
            return [self.name_chip(p.who(m.get('sender')), f' {t}'), '', self.body(p, m)] + self.picture(p, chat, m)
        return None

    def name_chip(self, who, after):
        """Who wrote, coloured as the bar's chips are: you as its first, everyone else as the rest."""
        colours = ('home_fg', 'home_bg') if who == 'You' else ('seg_fg', 'seg_bg')
        return fc.Chips([(who, *colours, True)], after)

    def picture(self, p, chat, m):
        """A photo message's picture, for the pane; looking at one not fetched yet fetches it."""
        f = m.get('file') or {}
        if not (f.get('type') or '').startswith('image/'):
            return []
        if f.get('status') == 1 and f.get('path') and os.path.exists(f['path']):
            return [fc.Picture(f['path'])]
        if f.get('status') in (0, 3):
            with self.serve.lock:
                ask = (chat, m['id']) not in p.fetching
                p.fetching.add((chat, m['id']))
            if ask:
                self.serve.request('download_file', profile=p.id, chat=chat, message=m['id'])
        return ['(fetching the picture…)' if f.get('status') != 3 else '(the picture couldn\'t be fetched)']

    def body(self, p, m):
        """A message as text: what it quotes, what it says, its file and reactions."""
        out = []
        q = m.get('quoted')
        if q and (q.get('text') or q.get('id')):
            out.append(f'↳ {p.who(q.get("sender"))}: {(q.get("text") or "").splitlines()[0] if q.get("text") else "…"}')
        if m.get('deleted'):
            out.append('(deleted)')
        elif m.get('text'):
            out.append(m['text'] + (' (edited)' if m.get('edited') else ''))
        f = m.get('file')
        if f:
            state = {0: 'not downloaded', 1: f.get('path', ''), 2: 'downloading', 3: 'download failed'}
            out.append(f'📎 {f.get("type") or "file"}: {state.get(f.get("status"), "")}')
        counts = (m.get('reactions') or {}).get('emoji_counts') or {}
        if counts:
            out.append('  '.join(f'{e} {n}' for e, n in counts.items()))
        return '\n'.join(out) or '(empty)'

    def choose(self, node):
        return None

    # -- writing, and files
    def actions(self, node):
        if node.kind not in ('chat', 'chat-message'):
            return []
        out = [('\x0e', 'write', '⌃N write'), ('\x01', 'attach', '⌃A send a file')]
        if node.kind == 'chat-message':
            out.insert(1, ('\x12', 'reply', '⌃R reply'))
            if node.data[2].get('file'):
                out += [('\x0f', 'open', '⌃O open file'), ('\x13', 'save', '⌃S save file')]
        return out

    def act(self, node, name, canvas):
        p, chat = node.data[:2]
        m = node.data[2] if node.kind == 'chat-message' else None
        to = p.chat_name(chat)
        if name == 'write':
            canvas.ask(f'to {to}', lambda text: self.send(canvas, p, chat, text))
        elif name == 'reply':
            canvas.ask(f'reply to {p.who(m.get("sender"))}',
                       lambda text: self.send(canvas, p, chat, text, quoted=m['id']))
        elif name == 'attach':
            def choose():
                path = self.pick(files.downloads())
                if path:
                    canvas.ask(f'{os.path.basename(path)} to {to}, saying (or nothing)',
                               lambda text: self.send(canvas, p, chat, text, file=path, empty_ok=True))
            canvas.outside(choose)
        elif name in ('open', 'save'):
            use = (lambda path: files.open_file(path)) if name == 'open' else \
                  (lambda path: files.copy_to(path, files.downloads()))
            f = m.get('file') or {}
            if f.get('status') == 1 and f.get('path') and os.path.exists(f['path']):
                use(f['path'])
                canvas.say('opening' if name == 'open' else f'saved to {files.downloads()}')
            else:
                with self.serve.lock:
                    p.wanted[(chat, m['id'])] = use
                self.serve.request('download_file', profile=p.id, chat=chat, message=m['id'])
                canvas.say('fetching it: it ' + ('opens' if name == 'open' else 'is saved') + ' when it\'s here')
        return None

    def pick(self, start):
        return fc.pick(start, glyphs='plain')

    def send(self, canvas, p, chat, text, quoted=None, file=None, empty_ok=False):
        if not text.strip() and not empty_ok:
            canvas.say('nothing to send')
            return None
        fields = dict(profile=p.id, chat=chat, text=text)
        if quoted:
            fields['quoted'] = quoted
        if file:
            fields['file'] = file
        if self.serve.request('send_message', **fields) is None:
            canvas.say('not sent: ' + (self.serve.error or 'nchat is not running'))
        else:
            canvas.say('sending…')
        return None
