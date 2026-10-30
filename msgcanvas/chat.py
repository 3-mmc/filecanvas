"""Chats on the canvas: nchat's profiles (WhatsApp, Signal, Instagram, …), their chats (pinned,
then the latest first), and each chat's messages; the reading pane shows the conversation, newest
at the bottom. ⌃N writes in a chat, ⌃R replies to a message, ⌃A sends a file, ⌃O / ⌃S open or
save a message's file (fetching it first).

nchat runs as a child, `nchat --serve`, speaking JSON lines (nchat's doc/serve.md): requests go
in, replies and events come out. Everything it says is kept here, by profile, chat and message
id, and the canvas is invalidated where it changed. Nothing is marked read yet."""
import json, os, subprocess, threading, time, zlib

from . import files, treecanvas as fc
from .mail import SEP

PROTOCOLS = {'WhatsAppMd': 'WhatsApp', 'Signal': 'Signal', 'Instagram': 'Instagram',
             'Telegram': 'Telegram', 'Dummy': 'Dummy'}
HISTORY = 50          # messages fetched when a chat is first looked at
SHOWN = 20            # messages a chat shows on the canvas, the latest; ↑ earlier shows more
MORE = 20             # and how many more each time
MESSAGE_WIDTH = 46    # columns a message wraps to
TYPING = 12           # seconds someone is typing after the last word that they are


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
        self.typed = {}             # (chat, user): when they were last said to be typing, or to stop
        self.connected = None       # None until nchat says
        self.needs_setup = False
        self.unsent = {}            # chat: why the last message to it wasn't sent
        self.wanted = {}            # (chat, message): what to do with its file once it's downloaded
        self.fetching = set()       # (chat, message): pictures asked for, to show
        self.colours = {}           # chat: {sender: n}, person n's colour, in the order they first wrote

    def who(self, user):
        """A person's name: from contacts, else their own chat's (a one-to-one chat has their id),
        else as a person would write them."""
        if user == self.self_id or user == 'You':
            return 'You'
        named = self.contacts.get(user, {}).get('name')
        if not named and user in self.chats and self.chats[user].get('name') not in (None, '', user):
            named = self.chats[user]['name']
        return named or plain(user)

    def colour(self, chat, sender):
        """Which of the theme's colours for people `sender` has in `chat`: their own (from who they
        are, so the same in every chat and every time), unless someone in this chat has it already:
        then the next one free. You have the bar's own colours instead."""
        seen = self.colours.setdefault(chat, {})
        if sender not in seen:
            taken = {n for s, n in seen.items() if self.who(s) != 'You'}
            own = preferred(sender)
            seen[sender] = next(((own + i) % fc.PEOPLE for i in range(fc.PEOPLE)
                                 if (own + i) % fc.PEOPLE not in taken), own)
        return seen[sender]

    def chat_name(self, chat):
        c = self.chats.get(chat, {})
        name = c.get('name') or chat
        return self.who(chat) if name == chat else name   # named by its id: a contact's name, once known


def preferred(ident):
    """Someone's own colour (or a group's): one of the theme's people, the same every time."""
    return zlib.crc32((ident or '').encode()) % fc.PEOPLE


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
        self.state(on_change)
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

    def state(self, on_change):
        self.on_change, self.lock = on_change, threading.Lock()
        self.profiles, self.skipped, self.error, self.next_id = {}, [], None, 0
        self.ready = threading.Event()
        self.then = []              # what an event set off, to do once the lock is let go
        self.on_event = None        # on_event(kind, profile, chat, what): arrived, sent, typing
        self.started = time.time() * 1000
        self.proc = None

    @classmethod
    def detached(cls, on_change=lambda path: None):
        """One with no nchat behind it: its state, for feeding events by hand (tests)."""
        s = cls.__new__(cls)
        s.state(on_change)
        return s

    def tell(self, kind, p, chat, what):
        """on_event(...), once the lock is let go: it may ask nchat things."""
        if self.on_event:
            self.then.append(lambda: self.on_event(kind, p, chat, what))

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
            # new to you: not seen, not yours, and come since msgcanvas started (not history)
            fresh = [m for m in got if kind == 'messages' and m.get('id') and m['id'] not in msgs and
                     not m.get('outgoing') and m.get('time', 0) >= self.started - 60000]
            if fresh:
                self.tell('arrived', p, chat, fresh)
            if kind == 'sent':
                self.tell('sent', p, chat, e)
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
            p.typed[(chat, e.get('user'))] = time.time()
            self.tell('typing', p, chat, (e.get('user'), bool(e.get('typing'))))
            return [node]
        if kind in ('pinned', 'muted', 'archived') and e.get('ok', True) and chat in p.chats:
            if 'message' in e and e['message'] in msgs:
                msgs[e['message']]['pinned'] = e.get('on')
                return [node]
            p.chats[chat][kind] = e.get('on')
            return [top]
        return []


def ticks(m):
    """✓ there, ✓✓ read: for a message of yours."""
    return '✓✓' if m.get('read') else '✓'


def stamp(seconds, now=None):
    """When a message came, time first: 14:32 · today, · yesterday, · 5 Oct, · 5 Oct 2024."""
    t, now = time.localtime(seconds), time.localtime(now or time.time())
    clock = time.strftime('%H:%M', t)
    if t[:3] == now[:3]:
        return f'{clock} · today'
    if t[:3] == time.localtime(time.mktime(now) - 86400)[:3]:
        return f'{clock} · yesterday'
    day = f'{t.tm_mday} {time.strftime("%b", t)}'
    return f'{clock} · {day}' if t.tm_year == now.tm_year else f'{clock} · {day} {t.tm_year}'


def profile_id(profile):
    return '/@' + profile


def chat_id(profile, chat):
    return f'{profile_id(profile)}{SEP}{chat}'


class Chat(fc.Provider):
    """nchat's profiles → chats → messages. `KINDS` are the node kinds it answers for."""
    KINDS = ('profile', 'chat', 'run', 'chat-message', 'earlier')
    pane = True

    def __init__(self, nchat='nchat', conf=None, log=None, read_receipts=True, typing=True, notify=True,
                 serve=None):
        self.read_receipts, self.send_typing, self.notify = read_receipts, typing, notify
        self.sending = {}       # request id: (status key, profile, chat, who to, timer)
        self.looking = None     # (profile, chat) you're in, or None
        self.typed = {}         # (profile, chat): when your typing was last told
        self.told = {}          # chat: when a notification about it was last shown
        self.shown = {}         # (profile, chat): how many of its messages the canvas shows
        self.serve = serve or Serve(lambda path: self.invalidate(path), nchat, conf, log)   # serve: tests' own
        self.serve.on_event = self.on_event

    def root(self):
        return fc.Node('', 'chat', True, source=self, kind='root')

    def loading(self):
        """(what's loading, whether it's done), for the opening screen: each profile connecting,
        then how many chats it has."""
        s = self.serve
        if s.error:
            return s.error, True
        if not s.ready.is_set():
            return 'starting nchat…', False
        parts, done = [], True
        for p in s.profiles.values():
            name = PROTOCOLS.get(p.protocol, p.protocol)
            if p.chats:
                parts.append(f'{name} · {len(p.chats)} chats')
            else:
                parts.append(f'{name} connecting…')
                done = done and p.connected is False      # it said it can't: no use waiting
        return '  ·  '.join(parts) or 'no chat accounts', done

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
            return self.conversation(node)
        if node.kind == 'run':
            p, chat, msgs, _ = node.data
            # a message's id doesn't depend on its run, so it's found again when runs regroup
            return [fc.Node(f'{node.parent.path}{SEP}{m["id"]}', self.message_label(p, m), False, node,
                            'chat-message', data=(p, chat, m)) for m in msgs]
        return []

    def conversation(self, node):
        """A chat's latest messages, oldest at the top, in runs: who, then what they said in a row.
        Above them, '↑ earlier' shows more."""
        p, chat = node.data
        msgs = self.history(p, chat)
        shown = self.shown.get((p.id, chat), SHOWN)
        older = max(0, len(msgs) - shown)
        out = [fc.Node(f'{node.path}{SEP}earlier', f'↑ {older} earlier' if older else '↑ earlier', False, node,
                       'earlier', data=(p, chat))] if msgs else []
        runs, day = [], None
        for m in msgs[older:]:
            sender = m.get('sender')
            if runs and runs[-1][0] == sender:
                runs[-1][1].append(m)
            else:
                runs.append((sender, [m]))
        for sender, run in runs:
            when = stamp(run[0]['time'] / 1000).split(' · ')[1] if run[0].get('time', -1) > 0 else ''
            name = p.who(sender) + (f'  {when}' if when != day else '')    # the day, when it changes
            day = when
            r = fc.Node(f'{node.path}{SEP}run{SEP}{run[0]["id"]}', name, True, node, 'run',
                        data=(p, chat, run, sender))
            r.open = True                       # a conversation shows what everyone said, not just one
            out.append(r)
        if not msgs:
            raise OSError(0, 'fetching…' if chat in p.asked else 'no messages')
        return out

    def message_label(self, p, m):
        """A message as it's drawn on the canvas: what it says (quote, text, file, reactions),
        then its time and, if it's yours, ✓ or ✓✓."""
        end = time.strftime('%H:%M', time.localtime(m['time'] / 1000)) if m.get('time', -1) > 0 else ''
        if m.get('outgoing') and not m.get('deleted'):
            end += ' ' + ticks(m)
        return self.body(p, m) + ('  ' + end if end else '')

    def history(self, p, chat):
        """The chat's messages, oldest first; the first look asks nchat for the latest."""
        with self.serve.lock:
            ask = chat not in p.asked
            p.asked.add(chat)
            msgs = sorted(p.messages.get(chat, {}).values(), key=lambda m: m.get('time', -1))
        if ask:
            self.serve.request('get_messages', profile=p.id, chat=chat, **{'from': ''}, limit=HISTORY)
        return msgs

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

    def width(self, node):
        return MESSAGE_WIDTH if node.kind == 'chat-message' else None

    def chip(self, node):
        """A run's name in its writer's colour (you: the bar's first chip's), and a chat that's
        open in its own: the other person's, or the group's, so open chats stand apart."""
        if node.kind == 'chat' and node.open:
            p, chat = node.data
            if p.who(chat) == 'You':
                return 'home_fg', 'home_bg'
            return 'person_fg', f'person{preferred(chat)}'
        if node.kind != 'run':
            return None
        p, chat, _, sender = node.data
        if p.who(sender) == 'You':
            return 'home_fg', 'home_bg'
        return 'person_fg', f'person{p.colour(chat, sender) % fc.PEOPLE}'

    def start_at(self, node):
        return -1 if node.kind in ('chat', 'run') else 0       # the latest, and keep to it

    def pane_for(self, node):
        """Chats are on the canvas itself: the pane only shows a photo."""
        if node.kind == 'chat-message':
            return ((node.data[2].get('file') or {}).get('type') or '').startswith('image/')
        return node.kind not in self.KINDS

    def bar(self, node, plain):
        names, n = [], node
        while n.parent:
            names.append(fc.clip((n.data[2].get('text') or '📎').split('\n')[0], 30)
                         if n.kind == 'chat-message' else self.label(n).split('  ')[0])
            n = n.parent
        names.reverse()
        segs = [('chat', 'home_fg', 'home_bg', False)] + [
            (t, 'cwd_fg' if i == len(names) - 1 else 'seg_fg', 'seg_bg', i == len(names) - 1)
            for i, t in enumerate(names)]
        tail = []
        if node.kind in ('chat', 'run', 'chat-message', 'earlier'):
            p, chat = node.data[:2]
            if chat in p.unsent:
                tail.append(('⚠ ' + p.unsent[chat], 'repo_dirty_fg', 'repo_dirty_bg', False))
        return segs, tail

    # -- the reading pane: a photo's picture
    def preview(self, node):
        if node.kind == 'profile':
            p = node.data
            if not isinstance(p, Profile):
                return f'{node.name}: {p["skipped"]}' + (
                    '\n\nnchat is open on it: close nchat, then open this again.' if p['skipped'] == 'locked' else '')
            state = {None: 'connecting…', True: 'connected', False: 'not connected'}[p.connected]
            return f'{p.protocol} {p.name}\n{state}\n\n{len(p.chats)} chats'
        if node.kind == 'chat-message':
            p, chat, m = node.data
            return self.picture(p, chat, m) or None
        return None

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
        """Enter on '↑ earlier': show more, fetching them first if they aren't here yet."""
        if node.kind == 'earlier':
            p, chat = node.data
            key = (p.id, chat)
            have = len(p.messages.get(chat, {}))
            self.shown[key] = self.shown.get(key, SHOWN) + MORE
            if self.shown[key] > have and have:
                oldest = min(p.messages[chat].values(), key=lambda m: m.get('time', -1))
                self.serve.request('get_messages', profile=p.id, chat=chat, **{'from': oldest['id']}, limit=MORE)
            self.invalidate(chat_id(p.id, chat))
        return None

    # -- writing, and files
    def actions(self, node):
        if node.kind not in ('chat', 'run', 'chat-message', 'earlier'):
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
            canvas.ask(f'to {to}', lambda text: self.send(canvas, p, chat, text), changed=self.typing_in(p, chat))
        elif name == 'reply':
            canvas.ask(f'reply to {p.who(m.get("sender"))}',
                       lambda text: self.send(canvas, p, chat, text, quoted=m['id']), changed=self.typing_in(p, chat))
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
        n = self.serve.request('send_message', **fields)
        if n is None:
            canvas.status('send', 'not sent: ' + (self.serve.error or 'nchat is not running'), 'failed')
            return None
        to = p.chat_name(chat)
        key = f'send-{n}'
        timer = threading.Timer(30, self.no_answer, args=(n,))
        timer.daemon = True
        self.sending[n] = (key, p, chat, to, timer)
        timer.start()
        self.status(key, f'to {to}', 'busy')
        return None

    def no_answer(self, n):
        sent = self.sending.pop(n, None)
        if sent:
            self.status(sent[0], f'no answer sending to {sent[3]}', 'failed')

    def typing_in(self, p, chat):
        """For the typing line: tell the chat you're typing while there's text, and when you stop."""
        def changed(text):
            if not self.send_typing:
                return
            last = self.typed.get((p.id, chat))
            if text and (last is None or time.time() - last > 8):       # again now and then, as apps do
                self.typed[(p.id, chat)] = time.time()
                self.serve.request('send_typing', profile=p.id, chat=chat, typing=True)
            elif not text and last is not None:
                del self.typed[(p.id, chat)]
                self.serve.request('send_typing', profile=p.id, chat=chat, typing=False)
        return changed

    # -- where you are, and what arrives
    def focus(self, node):
        if node.kind in ('chat', 'run', 'chat-message', 'earlier'):
            p, chat = node.data[:2]
            self.looking = (p.id, chat)
            if node.kind == 'chat':
                self.history(p, chat)           # ask for it now: it's there by the time you go in
            if node.kind != 'chat':             # in it, not passing by: it's read
                self.mark_read(p, chat)
        else:
            self.looking = None

    def mark_read(self, p, chat):
        with self.serve.lock:
            unread = [m for m in p.messages.get(chat, {}).values() if not m.get('outgoing') and not m.get('read')]
            was = p.chats.get(chat, {}).get('unread')
            for m in unread:
                m['read'] = True
            if chat in p.chats:
                p.chats[chat]['unread'] = False
        if self.read_receipts:
            for m in unread:
                self.serve.request('mark_read', profile=p.id, chat=chat, message=m['id'])
        if unread or was:
            self.invalidate(profile_id(p.id))

    def on_event(self, kind, p, chat, what):
        """From the reader thread: what nchat said that the canvas should show moving."""
        name = p.chat_name(chat)
        if kind == 'sent':
            e = what
            mine = [n for n, s in self.sending.items() if (n == e.get('request')) or
                    ('request' not in e and s[2] == chat)]
            if not mine:
                return
            key, _, _, to, timer = self.sending.pop(min(mine))
            timer.cancel()
            self.status(key, f'sent to {to}' if e.get('ok', True) else f'not sent to {to}',
                        'ok' if e.get('ok', True) else 'failed')
        elif kind == 'arrived':
            if self.looking == (p.id, chat):    # you're in it: what came in flashes as it appears
                for m in what:
                    self.pulse(f'{chat_id(p.id, chat)}{SEP}{m["id"]}')
                return
            last = what[-1]
            line = f'{p.who(last.get("sender"))}: {(last.get("text") or "📎").splitlines()[0] if last.get("text") else "📎"}'
            self.pulse(chat_id(p.id, chat))
            self.pulse(profile_id(p.id))
            self.status(f'new-{p.id}-{chat}', f'{name} · {line}' if name != p.who(last.get('sender')) else line, 'note')
            muted = p.chats.get(chat, {}).get('muted')
            if self.notify and not muted and time.time() - self.told.get(chat, 0) > 10:
                self.told[chat] = time.time()
                files.notify(name, line if len(what) == 1 else f'{line}  (+{len(what) - 1} more)')
        elif kind == 'typing':
            user, on = what
            if user == p.self_id:
                return
            key = f'typing-{p.id}-{chat}'
            where = '' if self.looking == (p.id, chat) else f' in {name}'
            self.status(key, f'{p.who(user)} typing{where}', 'busy' if on else 'gone')
            if on:              # protocols that don't say when it stops: it stops by itself
                t = threading.Timer(TYPING, self.stop_typing, args=(p, chat, user, p.typed[(chat, user)], key))
                t.daemon = True
                t.start()

    def stop_typing(self, p, chat, user, since, key):
        """Typing nobody has said more about for TYPING seconds has stopped."""
        with self.serve.lock:
            if p.typed.get((chat, user)) != since:
                return                          # said again since: that one's timer will see to it
            p.typing.get(chat, set()).discard(user)
        self.status(key, f'{p.who(user)} typing', 'gone')
        self.invalidate(chat_id(p.id, chat))
