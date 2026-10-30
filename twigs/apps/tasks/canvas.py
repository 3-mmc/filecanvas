"""TuiDo on a canvas: the desk as one tree you move across (a twig: tuido's store, twigs' canvas).

Today and its earlier pages, Paths, Library, Habits, Skills, Projects and Ideas branch from the
root, each as deep as it goes: a path's modules and activities, a book's chapters, a project's
steps and subtasks. Pinned focus and the chosen shelf narrow it as they do the desk.

Everything goes through the Store, so each action is one transaction and one undo step:

    Enter   mark it (a task, activity, chapter, step or habit's day), or open it
    ⌃P      plan it into Today      ⌃N  add one beside it    ⌃A  add one inside it (a subtask)
    ⌥→ ⌥←   nest a step under the one above, or lift it out      ⌥↑ ⌥↓  move it
    ⌃E      rename      ⌃D  delete
    ⌃R      continue: to the next activity a path is ready for
    ⌃T      carry an unfinished task forward to today      ⌃Z  undo
    ⌃W      when a task's due ('fri', '3 nov', '+3d'): it says so, and calls once it's late
    ⌃S      a timer on it ('25', '1h30'): it counts down beside it, then calls

    twigs tasks [--db FILE | --demo]     (or tuido --canvas)
"""
from datetime import date, timedelta
import json
import math
import os
import threading
import time

try:
    from tuido.learning_ui import LEVELS
except ImportError:     # tuido's interface (textual) isn't here: its words for a skill's level
    LEVELS = ('Not yet', 'Explored', 'Practised', 'Demonstrated')

from twigs import core as tc
from twigs.kit import notify, when

STATE = {2: ('state_done', '✓', 'x'), 1: ('state_doing', '◐', '~'), 0: ('stub', '○', '.')}
COLOUR = {2: 'state_done', 1: 'state_doing'}
INFO_WIDTH = 44     # columns a note wraps to on the canvas
SECTIONS = (('today', 'Today'), ('pages', 'Earlier pages'), ('paths', 'Paths'), ('library', 'Library'),
            ('habits', 'Habits'), ('skills', 'Skills'), ('projects', 'Projects'), ('ideas', 'Ideas'))
TABLE = {'path': 'paths', 'activity': 'activities', 'book': 'books', 'chapter': 'chapters', 'habit': 'habits',
         'skill': 'skills', 'project': 'projects', 'task': 'project_tasks', 'idea': 'ideas', 'bullet': 'bullets'}
WEEK = 'MTWTFSS'


def boxes(states, width=24):
    """Progress as a bar under a title: a block per part (done █, under way ▓, not yet ░) with
    gaps between, or, with too many parts for that, one bar in proportion; then how many are done."""
    if not states:
        return ''
    n = len(states)
    per = min(4, (width + 1) // n - 1)
    glyph = {2: '█', 1: '▓'}
    if per >= 1:                                # a block a part, as long as each gets one
        bar = ' '.join(glyph.get(s, '░') * per for s in states)
    else:
        done, doing = sum(s == 2 for s in states), sum(s == 1 for s in states)
        a, b = round(width * done / n), round(width * doing / n)
        bar = '█' * a + '▓' * b + '░' * (width - a - b)
    return f'{bar}  {sum(s == 2 for s in states)}/{n}'


def ago(day, today):
    days = (today - date.fromisoformat(day)).days
    return 'today' if days == 0 else 'yesterday' if days == 1 else f'{days} days ago'


class Desk(tc.Provider):
    """The provider: nodes carry (kind, row) in data; ids are the way down, /paths/3/a12/a15.
    No reading pane: what there is to say about something is on the canvas beside it."""
    pane = False

    def __init__(self, store, path=None, watch=2.0):
        self.store, self.file = store, path
        if 'canvas_position' not in {r['name'] for r in store.rows('PRAGMA table_info(bullets)')}:
            store.db.execute('ALTER TABLE bullets ADD COLUMN canvas_position INTEGER')
            store.db.commit()
        self.today = date.today
        self.memo = {}          # snapshots, until something changes: a frame asks each node many times
        self.clock = time.time
        self.timers = {}        # node id: (when it ends, its name, the threading.Timer that rings it)
        self.rang = {}          # node id: when its timer ended, until you look at it
        if path and watch:
            threading.Thread(target=self.watch, args=(watch,), daemon=True).start()

    def watch(self, every):
        """The desk changed by someone else (TuiDo itself, open beside it): show it."""
        def stamp():
            try:
                return tuple(os.stat(p).st_mtime_ns if os.path.exists(p) else 0
                             for p in (self.file, f'{self.file}-wal'))
            except OSError:
                return None
        seen = stamp()
        while True:
            time.sleep(every)
            now = stamp()
            if now != seen:
                seen = now
                self.invalidate()

    def loading(self):
        """(what's loading, whether it's done), for the opening screen."""
        return 'your desk', True

    def invalidate(self, path=None, deep=False):
        self.memo.clear()           # anything may have changed: work it all out again
        super().invalidate(path, deep)

    def remember(self, key, work):
        if key not in self.memo:
            self.memo[key] = work()
        return self.memo[key]

    def learning(self, path_id):
        return self.remember(('path', path_id), lambda: self.store.learning_snapshot(path_id))

    def project(self, project_id):
        return self.remember(('project', project_id), lambda: self.store.project_snapshot(project_id, self.today()))

    def unmet(self, path_id, node_id):
        return self.remember(('unmet', path_id, node_id), lambda: self.store.unmet_prerequisites(path_id, node_id))

    # -- the tree
    def root(self):
        return tc.Node('', 'tuido', True, source=self, kind='root')

    def children(self, node):
        kids = self.parts(node)
        return kids if node.kind in ('root', 'info') else self.with_info(node, kids)

    def parts(self, node):
        s, k = self.store, node.kind
        if k == 'root':
            return [tc.Node('/' + key, name, True, node, key) for key, name in SECTIONS]
        if k in ('today', 'page'):
            day = self.today().isoformat() if k == 'today' else node.data
            return [self.bullet(b, node) for b in s.rows("SELECT * FROM bullets WHERE day=? ORDER BY canvas_position IS NULL, canvas_position, id", (day,))]
        if k == 'pages':
            days = s.rows("SELECT DISTINCT day FROM bullets WHERE day<? ORDER BY day DESC LIMIT 60",
                          (self.today().isoformat(),))
            return [tc.Node(f'{node.path}/{d["day"]}', self.day_name(d['day']), True, node, 'page', data=d['day'])
                    for d in days]
        if k == 'paths':
            return [self.make(node, 'path', p, f'p{p["id"]}', True) for p in s.sidebar_items('paths')]
        if k in ('path', 'activity'):
            path_id = node.data['path_id'] if k == 'activity' else node.data['id']
            nodes, children, _ = self.learning(path_id)
            under = node.data['id'] if k == 'activity' else None
            return [self.make(node, 'activity', nodes[i], f'a{i}', bool(children.get(i))) for i in children.get(under, [])]
        if k == 'library':
            return [self.make(node, 'book', b, f'b{b["id"]}', True) for b in s.sidebar_items('books')]
        if k == 'book':
            return [self.make(node, 'chapter', c, f'c{c["id"]}', False) for c in
                    s.rows("SELECT * FROM chapters WHERE book_id=? ORDER BY position,id", (node.data['id'],))]
        if k in ('habits', 'habit'):
            items = s.sidebar_items('habits')
            under = node.data['id'] if k == 'habit' else None
            mine = [h for h in items if h['sidebar_parent'] == under]
            return [self.make(node, 'habit', h, f'h{h["id"]}', any(x['sidebar_parent'] == h['id'] for x in items))
                    for h in mine]
        if k == 'skills':
            return [self.make(node, 'skill', sk, f's{sk["id"]}', False) for sk in s.sidebar_items('skills')]
        if k == 'projects':
            return [self.make(node, 'project', p, f'j{p["id"]}', True) for p in s.sidebar_items('projects')]
        if k in ('project', 'task'):
            project = node.data['project_id'] if k == 'task' else node.data['id']
            tasks, children, _ = self.project(project)
            under = node.data['id'] if k == 'task' else None
            return [self.make(node, 'task', tasks[i], f't{i}', bool(children.get(i))) for i in children.get(under, [])]
        if k == 'ideas':
            return [self.make(node, 'idea', i, f'i{i["id"]}', False) for i in s.sidebar_items('ideas')]
        return []

    def make(self, parent, kind, row, key, branch):
        node = tc.Node(f'{parent.path}/{key}', row.get('title') or '(untitled)', branch, parent, kind, data=row)
        if not branch and self.info(node):          # something to say about it: it opens onto that
            node.is_dir = True
        return node

    def info(self, node):
        """What there is to say about a node, as lines of notes for the canvas beside it."""
        return self.remember(('info', node.path), lambda: self.about(node))

    def about(self, node):
        k, d, s = node.kind, node.data, self.store
        out = []
        if k in ('today', 'page'):
            day = self.today().isoformat() if k == 'today' else d
            rows = s.rows("SELECT * FROM bullets WHERE day=?", (day,))
            tasks = [b for b in rows if not b['note']]
            if rows:
                out.append(f'{sum(b["done"] for b in tasks)} of {len(tasks)} done'
                           + (f', {len(rows) - len(tasks)} notes' if len(rows) > len(tasks) else ''))
        elif k == 'bullet':
            linked = self.linked(d)
            if linked:
                out.append(linked)
            if d['migrated']:
                out.append('carried forward')
        elif k == 'path':
            if d.get('description'):
                out.append(d['description'])
            nxt = s.next_activity(d['id'])
            if nxt:
                out.append('next: ' + s.rows("SELECT title FROM activities WHERE id=?", (nxt,))[0]['title'] + '  (⌃R)')
        elif k == 'activity':
            if d['optional']:
                out.append('optional')
            if d['resource_uid']:
                res = s.rows("SELECT title, url FROM resources WHERE uid=?", (d['resource_uid'],))
                if res:
                    out.append('source: ' + res[0]['title'] + (f'  {res[0]["url"]}' if res[0]['url'] else ''))
            missing = self.unmet(d['path_id'], d['id'])
            if missing:
                out.append('suggested first: ' + ', '.join(missing))
            if d['notes']:
                out.append(d['notes'])
        elif k == 'chapter' and s.rows("SELECT textbook FROM books WHERE id=?", (d['book_id'],))[0]['textbook']:
            out.append('exercises: ' + ('done' if d['exercises'] else 'not yet'))
        elif k == 'habit':
            out.append(self.heatmap(d))
        elif k == 'skill' and d['evidence']:
            out.append('evidence: ' + d['evidence'])
        elif k == 'project':
            if d['notes']:
                out.append(d['notes'])
            nxt = s.next_task(d['id'])
            if nxt:
                out.append('next: ' + s.rows("SELECT title FROM project_tasks WHERE id=?", (nxt,))[0]['title'])
        elif k in ('task', 'idea'):
            if d.get('notes'):
                out.append(d['notes'])
            if k == 'idea' and d.get('project_id'):
                taken = s.rows("SELECT title FROM projects WHERE id=?", (d['project_id'],))
                if taken:
                    out.append(f'taken up in {taken[0]["title"]}')
        return out

    def holds(self, node):
        """Whether it has parts of its own (a module's activities, a step's subtasks), not just notes."""
        if node.kind == 'activity':
            return bool(self.learning(node.data['path_id'])[1].get(node.data['id']))
        if node.kind == 'task':
            return bool(self.project(node.data['project_id'])[1].get(node.data['id']))
        return node.kind not in ('chapter', 'bullet', 'skill', 'idea', 'habit') or \
            (node.kind == 'habit' and any(h['sidebar_parent'] == node.data['id'] for h in self.store.sidebar_items('habits')))

    def with_info(self, node, kids):
        """A node's notes first, quiet, then its children."""
        notes = [tc.Node(f'{node.path}/info{n}', text, False, node, 'info', data=text)
                 for n, text in enumerate(self.info(node))]
        return notes + kids

    def bullet(self, b, parent):
        text = ('— ' if b['note'] else '') + b['text']
        return tc.Node(f'{parent.path}/{b["id"]}', text, False, parent, 'bullet', data=b)

    def day_name(self, day):
        d = date.fromisoformat(day)
        return ('yesterday · ' if d == self.today() - timedelta(days=1) else '') + d.strftime('%a %-d %b %Y')

    # -- how it looks
    def parts_states(self, node):
        """The states of a path's modules, a book's chapters, a project's steps."""
        k, d = node.kind, node.data
        if k == 'path':
            _, children, states = self.learning(d['id'])
            return [states[i] for i in children.get(None, [])]
        if k == 'book':
            return [c['reading'] for c in self.remember(('chapters', d['id']), lambda: self.store.rows(
                "SELECT reading FROM chapters WHERE book_id=? ORDER BY position,id", (d['id'],)))]
        if k == 'project':
            _, children, states = self.project(d['id'])
            return [states[i] for i in children.get(None, [])]
        return []

    def state(self, node):
        """2 done, 1 under way, 0 not yet, or None for what has no state."""
        k, d = node.kind, node.data
        if k in ('path', 'book', 'project'):
            parts = self.parts_states(node)
            return None if not parts else 2 if all(p == 2 for p in parts) else 1 if any(parts) else 0
        if k == 'activity':
            return self.learning(d['path_id'])[2].get(d['id'], 0)
        if k == 'chapter':
            return d['reading']
        if k == 'task':
            return self.project(d['project_id'])[2].get(d['id'], 0)
        if k == 'bullet' and not d['note']:
            return 2 if d['done'] else None
        return None

    def label(self, node):
        k, d = node.kind, node.data
        if k == 'today':
            return f'Today · {self.today().strftime("%a %-d %b")}'
        if k in ('path', 'book', 'project'):
            bar = boxes(self.parts_states(node))
            return node.name + (f'\n{bar}' if bar else '')
        if k == 'habit':
            return f'{node.name}\n{self.week(d)}'
        if k == 'skill':
            return f'{node.name} · {LEVELS[d["level"]]}'
        if k == 'task' and d['every_days']:
            when = ago(d['last_done'], self.today()) if d['last_done'] else 'not yet'
            return f'{node.name} · every {d["every_days"]} days · {when}'
        if k == 'chapter' and d['exercises']:
            return f'{node.name} · exercises ✓'
        return node.name

    def week(self, habit):
        """This Monday-to-Sunday week, a box a day: met ■, some □, none ·, still to come blank."""
        today = self.today()
        monday = today - timedelta(days=today.weekday())
        counts = self.store.habit_counts(habit['id'], planned=False)
        goal = habit['daily_goal'] if habit['goal_period'] == 'day' else 1
        out = []
        for n in range(7):
            day = monday + timedelta(days=n)
            got = counts.get(day.isoformat(), 0)
            out.append(' ' if day > today else '■' if got >= goal else '□' if got else '·')
        return ' '.join(out).rstrip()

    def mark(self, node):
        """Before its title (mark_before): what state it's in, or what it is."""
        k = node.kind
        if k in ('info', 'root') or k in dict(SECTIONS):
            return None
        if k == 'bullet' and node.data['migrated']:
            return 'stub', '›', '>'
        if k == 'bullet' and node.data['note']:
            return 'stub', '–', '-'
        if k == 'idea' and node.data.get('project_id'):
            return 'state_done', '↳', '>'
        if k in ('path', 'book', 'habit', 'skill', 'project', 'idea') and node.data.get('pinned'):
            return 'state_doing', '★', '*'
        state = self.state(node)
        if state is None and k in ('bullet', 'habit', 'skill', 'idea'):
            state = 0
        return STATE.get(state)

    def mark_before(self, node):
        return True

    # -- time: due days, timers
    def due(self, node):
        d = node.data if node.kind == 'task' else None
        return date.fromisoformat(d['due']) if d and d.get('due') else None

    def late(self, node):
        """When a task became late (the midnight after its due day), if it's due, unfinished and
        that's gone; else None."""
        due = self.due(node)
        if due is None or due >= self.today() or self.state(node) == 2:
            return None
        return time.mktime((due + timedelta(days=1)).timetuple())

    def count(self, node):
        """A timer counting down ('⏱ 23m'), then when it's due ('due fri', '2 days late')."""
        parts, role, bold = [], 'stub', False
        if node.path in self.timers:
            left = self.timers[node.path][0] - self.clock()
            parts.append(f'⏱ {max(1, math.ceil(left / 60))}m')
            role, bold = 'state_doing', True
        due = self.due(node)
        if due is not None and self.state(node) != 2:
            parts.append(when.due_text(due, self.today()))
            if due < self.today():
                role, bold = 'git_conflict', True
            elif due == self.today() and role == 'stub':
                role, bold = 'state_doing', True
        return ('  '.join(parts), role, bold) if parts else None

    def beacon(self, node):
        """A task gone late, from when it did; a project holding one; a timer that's rung, till you
        look at it."""
        got = [self.rang.get(node.path), self.late(node)]
        if node.kind == 'project' and hasattr(self.store, 'overdue'):
            today = self.today()
            got += [time.mktime((date.fromisoformat(r['due']) + timedelta(days=1)).timetuple())
                    for r in self.store.overdue(node.data['id'], today.isoformat())]
        got = [t for t in got if t]
        return max(got) if got else None

    def focus(self, node):
        if self.rang.pop(node.path, None) is not None:
            self.status('timer', '', 'gone')

    def next_change(self):
        """A timer's next minute down, or midnight (what's due today, tomorrow, late, moves)."""
        now = self.clock()
        at = [time.mktime((self.today() + timedelta(days=1)).timetuple())]
        for end, _, _ in self.timers.values():
            left = end - now
            at.append(now + (left % 60 or 60) if left > 0 else end)
        return min(at)

    def start_timer(self, node, minutes):
        self.stop_timer(node)
        end = self.clock() + minutes * 60
        t = threading.Timer(minutes * 60, self.ring, args=(node.path, node.name, end))
        t.daemon = True
        self.timers[node.path] = (end, node.name, t)
        t.start()

    def stop_timer(self, node):
        got = self.timers.pop(node.path, None)
        if got:
            got[2].cancel()
        return got

    def ring(self, path, name, end):
        """A timer's time is up (from its thread): told, and its row a beacon till you look."""
        if self.timers.get(path, (None,))[0] != end:
            return                  # stopped, or started again since
        del self.timers[path]
        self.rang[path] = end
        notify.notify("Time's up", name.split('\n')[0])
        self.status('timer', f"time's up: {name.split(chr(10))[0]}", 'note')
        self.pulse(path)

    def colour(self, node):
        """Finished green, under way amber (the theme's), notes quiet."""
        if node.kind == 'info':
            return 'stub', False
        role = COLOUR.get(self.state(node))
        return (role, False) if role else None

    def line(self, node):
        return COLOUR.get(self.state(node))

    def width(self, node):
        return INFO_WIDTH if node.kind == 'info' and '\n' not in node.data else None

    def start_at(self, node):
        """Going in lands past the notes, on the first thing itself."""
        return len(self.info(node)) if node.kind not in ('root', 'info') else 0

    def dim(self, node):
        k, d = node.kind, node.data
        if k == 'bullet' and (d['done'] or d['migrated']):
            return 0.6
        if k == 'activity' and d['optional']:
            return 0.75
        if k == 'activity' and self.state(node) != 2 and self.unmet(d['path_id'], d['id']):
            return 0.6                      # suggested to wait: it isn't locked, just quieter
        return 1.0

    def heatmap(self, habit):
        """Twelve weeks, a row a weekday, as the desk's: met ■, some □, none ·."""
        today = self.today()
        start = today - timedelta(days=today.weekday() + 7 * 11)
        counts = self.store.habit_counts(habit['id'], planned=False)
        goal = habit['daily_goal'] if habit['goal_period'] == 'day' else 1
        rows = []
        for wd in range(7):
            cells = []
            for week in range(12):
                day = start + timedelta(days=7 * week + wd)
                got = counts.get(day.isoformat(), 0)
                cells.append(' ' if day > today else '■' if got >= goal else '□' if got else '·')
            rows.append(f'{WEEK[wd]} {" ".join(cells)}')
        goal_text = (f'{habit["daily_goal"]} {habit["unit"]} a day, {habit["weekly_goal"]} days a week'
                     if habit['goal_period'] == 'day' else f'{habit["daily_goal"]} {habit["unit"]} a week')
        return goal_text + '\n\n' + '\n'.join(rows)

    def linked(self, b):
        s = self.store
        for column, sql in (('activity_id', "SELECT title FROM activities WHERE id=?"),
                            ('chapter_id', "SELECT title FROM chapters WHERE id=?"),
                            ('project_task_id', "SELECT title FROM project_tasks WHERE id=?"),
                            ('habit_id', "SELECT title FROM habits WHERE id=?")):
            if b.get(column):
                row = s.rows(sql, (b[column],))
                if row:
                    return f'For: {row[0]["title"]}'
        return ''

    # -- doing
    def choose(self, node):
        """Enter on something with a state marks it; on a branch it opens (the canvas does)."""
        s, k, d = self.store, node.kind, node.data
        try:
            if k == 'bullet':
                s.toggle_bullet(d['id'])
            elif k == 'activity' and not self.holds(node):
                s.cycle_activity(d['id'])
            elif k == 'chapter':
                s.cycle_chapter(d['id'])
            elif k == 'task' and not self.holds(node):
                if s.cycle_task(d['id']) is False:
                    self.status('mark', f'{node.name}: done already, back in {d["every_days"]} days', 'note')
                    return tc.STAY
            elif k == 'habit':
                s.checkin(d['id'], self.today().isoformat(), 1)
            elif k == 'info' or not node.is_dir:
                return tc.STAY
            else:
                return None                     # a branch with no state of its own: it opens
        except ValueError as e:
            self.status('mark', str(e), 'failed')
            return tc.STAY
        self.changed_here(node)
        return tc.STAY                          # marked: it stays as it was, not opened onto its notes

    def changed_here(self, node):
        """After a change: everything again (one query each), and the node flashes."""
        self.invalidate()
        self.pulse(node.path)

    def actions(self, node):
        k = node.kind
        out = []
        if (k in ('activity', 'task') and not self.holds(node)) or k in ('chapter', 'habit'):
            out.append(('\x10', 'plan', '⌃P plan'))
        if k in ('today', 'page', 'paths', 'path', 'activity', 'library', 'book', 'habits', 'habit', 'projects',
                 'project', 'task', 'ideas', 'bullet'):
            out.append(('\x0e', 'new', '⌃N add'))
        if k in ('task', 'activity', 'habit'):
            out.append(('\x01', 'inside', '⌃A add inside'))
        if k == 'bullet':
            out += [('alt-up', 'up', '⌥↑↓ move'), ('alt-down', 'down', '')]
        if k == 'task':
            out += [('alt-right', 'nest', '⌥→ nest'), ('alt-left', 'lift', '⌥← lift'),
                    ('alt-up', 'up', '⌥↑↓ move'), ('alt-down', 'down', '')]
        if k in TABLE:
            out += [('\x05', 'rename', '⌃E rename'), ('\x04', 'delete', '⌃D delete')]
        if k in ('path', 'activity'):
            out.append(('\x12', 'continue', '⌃R continue'))
        if k == 'task' and hasattr(self.store, 'set_due'):      # a tuido with due days
            out.append(('\x17', 'due', '⌃W due…'))
        if k in ('task', 'activity', 'habit', 'bullet', 'chapter'):
            out.append(('\x13', 'timer', '⌃S stop timer' if node.path in self.timers else '⌃S timer'))
        if k == 'bullet' and not node.data['done'] and not node.data['note'] and not node.data['migrated'] \
                and node.data['day'] != self.today().isoformat():
            out.append(('\x14', 'today', '⌃T to today'))
        return out + [('\x1a', 'undo', '⌃Z undo')]

    def act(self, node, name, canvas):
        s, k, d = self.store, node.kind, node.data
        today = self.today().isoformat()

        def attempt(fn, done=None):
            try:
                fn()
            except ValueError as e:
                canvas.status('act', str(e), 'failed')
                return None
            self.changed_here(node)
            if done:
                canvas.status('act', done, 'ok')
            return None

        def typed(make, done=None):
            """For the typing line: what's typed, if anything, made into something. It returns None
            whatever happens: anything else would close the canvas."""
            return lambda text: attempt(lambda: make(text.strip()), done) if text.strip() else None

        if name == 'due':
            def due(text):
                if not text.strip() or text.strip().lower() in ('none', 'no', '-'):
                    return attempt(lambda: s.set_due(d['id'], None), 'not due any more')
                got = when.day(text, self.today())
                if got is None:
                    canvas.status('act', f'when is that? say {when.DAY_SAYS}', 'failed')
                    return None
                return attempt(lambda: s.set_due(d['id'], got.isoformat()), when.due_text(got, self.today()))
            now = self.due(node)
            canvas.ask(f'due ({when.DAY_SAYS}; nothing: not due)', due, text=f'{now.day} {now:%b}' if now else '')
            return None
        if name == 'timer':
            if self.stop_timer(node):
                canvas.status('act', 'timer stopped', 'note')
                return None

            def start(text):
                got = when.minutes(text) if text.strip() else 25
                if got is None:
                    canvas.status('act', "how long? say 25, 45m or 1h30", 'failed')
                    return None
                self.start_timer(node, got)
                canvas.status('act', f'timer: {got} min', 'ok')
                return None
            canvas.ask('timer, minutes (25)', start)
            return None
        if name == 'undo':
            if s.undo():
                self.invalidate()
                canvas.status('act', 'undone', 'ok')
            else:
                canvas.status('act', 'nothing to undo', 'note')
        elif name == 'plan':
            default = f'Read: {node.name}' if k == 'chapter' else node.name
            ids = dict(activity_id=d['id']) if k == 'activity' else dict(chapter_id=d['id']) if k == 'chapter' \
                else dict(project_task_id=d['id']) if k == 'task' else dict(habit_id=d['id'])
            canvas.ask('plan for today', typed(lambda text: s.add_bullet(today, text, **ids), 'planned into Today'),
                       text=default)
        elif name == 'today':
            attempt(lambda: s.migrate(d['id'], today), 'carried to today')
        elif name == 'rename':
            table = TABLE[k]
            current = d['text'] if k == 'bullet' else d['title']
            canvas.ask('rename', typed(lambda text: s.rename(table, d['id'], text)), text=current)
        elif name == 'delete':
            what = node.name

            def gone(key):
                if key != 'y':
                    return
                if k == 'activity':
                    attempt(lambda: s.archive_activity(d['id']))
                else:
                    attempt(lambda: s.delete(TABLE[k], d['id']))
                canvas.status('act', what, 'gone')
            canvas.ask(f'delete {tc.clip(what, 40)}?  y yes  n no  (⌃Z brings it back)', gone, choices='yn')
        elif name == 'new':
            self.new(node, canvas, typed)
        elif name == 'inside':
            self.inside(node, canvas, typed)
        elif name in ('up', 'down') and k == 'bullet':
            rows = s.rows('SELECT id FROM bullets WHERE day=? ORDER BY canvas_position IS NULL, canvas_position, id',
                          (d['day'],))
            ids = [r['id'] for r in rows]
            at = ids.index(d['id'])
            to = at + (-1 if name == 'up' else 1)
            if not 0 <= to < len(ids):
                canvas.status('act', 'first already' if name == 'up' else 'last already', 'note')
                return None
            ids[at], ids[to] = ids[to], ids[at]
            with s.change():
                s.db.executemany('UPDATE bullets SET canvas_position=? WHERE id=?', enumerate(ids))
            self.changed_here(node)
            canvas.refresh()
            ids, at = [], node
            while at.parent is not None:
                ids.append(at.path)
                at = at.parent
            canvas.go(list(reversed(ids)))
        elif name in ('nest', 'lift', 'up', 'down'):
            moved = {'nest': lambda: s.indent_task(d['id']), 'lift': lambda: s.outdent_task(d['id']),
                     'up': lambda: s.move_task(d['id'], -1), 'down': lambda: s.move_task(d['id'], 1)}[name]()
            if not moved:
                canvas.status('act', {'nest': 'nothing above it to go under', 'lift': 'it is at the top already',
                                      'up': 'first already', 'down': 'last already'}[name], 'note')
                return None
            self.invalidate()
            canvas.refresh()                    # the tree as it is now, then back onto the step where it went
            if canvas.go(self.task_ids(d['project_id'], d['id'])):
                self.pulse(canvas.focus.path)
        elif name == 'continue':
            path_id = d['path_id'] if k == 'activity' else d['id']
            nxt = s.next_activity(path_id)
            if not nxt:
                canvas.status('act', 'nothing required is ready: see what is optional, or suggested first', 'note')
                return None
            nodes, _, _ = s.learning_snapshot(path_id)
            chain, at = [], nxt
            while at:
                chain.append(f'a{at}')
                at = nodes[at]['parent_id']
            ids, way = ['/paths', f'/paths/p{path_id}'], f'/paths/p{path_id}'
            for key in reversed(chain):
                way += '/' + key
                ids.append(way)
            if canvas.go(ids):                  # the camera flies there
                self.pulse(ids[-1])
        return None

    def task_ids(self, project_id, task_id):
        """The ids down to a step: /projects, /projects/j…, then each step it's in."""
        tasks, _, _ = self.project(project_id)
        chain, at = [], task_id
        while at:
            chain.append(f't{at}')
            at = tasks[at]['parent_id']
        ids, way = ['/projects', f'/projects/j{project_id}'], f'/projects/j{project_id}'
        for key in reversed(chain):
            way += '/' + key
            ids.append(way)
        return ids

    def inside(self, node, canvas, typed):
        """⌃A: one inside it: a subtask under a step, an activity under one, a habit under a habit."""
        s, k, d = self.store, node.kind, node.data
        if k == 'task':
            canvas.ask(f'a subtask of {tc.clip(node.name, 30)}', typed(lambda t: s.add_task(d['project_id'], t, d['id'])))
        elif k == 'activity':
            canvas.ask(f'inside {tc.clip(node.name, 30)}', typed(lambda t: s.add_activity(d['path_id'], t, parent_id=d['id'])))
        elif k == 'habit':                      # a child counts towards its parent: the same unit
            canvas.ask(f'a habit within {tc.clip(node.name, 30)}', typed(
                lambda t: s.add_habit(t, d['unit'], d['daily_goal'], d['weekly_goal'], d['goal_period'], d['id'])))

    def new(self, node, canvas, typed):
        """⌃N: add one where you are: to the page, the path, the book, the project…"""
        s, k, d = self.store, node.kind, node.data
        if k in ('today', 'page', 'bullet'):
            day = (d['day'] if k == 'bullet' else d if k == 'page' else self.today().isoformat())
            canvas.ask('add ("- " for a note)', typed(lambda t: s.add_bullet(day, t.strip())))
        elif k == 'paths':
            canvas.ask('a new path', typed(lambda t: s.add_path(t.strip())))
        elif k in ('path', 'activity'):
            path_id = d['path_id'] if k == 'activity' else d['id']
            parent = d['id'] if k == 'activity' and self.holds(node) else d.get('parent_id') if k == 'activity' else None
            canvas.ask('a new activity', typed(lambda t: s.add_activity(path_id, t.strip(), parent_id=parent)))
        elif k == 'library':
            canvas.ask('a new book (its chapters after a |, split by ;)', typed(
                lambda t: s.add_book(t.split('|')[0].strip(), [c.strip() for c in (t.split('|', 1)[1:] or [''])[0].split(';') if c.strip()])))
        elif k == 'book':
            canvas.ask('a new chapter', typed(lambda t: s.add_chapter(d['id'], t.strip())))
        elif k == 'habits':
            canvas.ask('a new habit', typed(lambda t: s.add_habit(t.strip())))
        elif k == 'habit':                      # beside it: under the same parent, in its unit if it has one
            parent = d.get('parent_id')
            canvas.ask('a new habit', typed(lambda t: s.add_habit(t.strip(), d['unit'], parent_id=parent) if parent
                                                else s.add_habit(t.strip())))
        elif k == 'projects':
            canvas.ask('a new project', typed(lambda t: s.add_project(t.strip())))
        elif k in ('project', 'task'):
            project = d['project_id'] if k == 'task' else d['id']
            parent = d['id'] if k == 'task' and self.holds(node) else d.get('parent_id') if k == 'task' else None
            canvas.ask('a new step', typed(lambda t: s.add_task(project, t.strip(), parent)))
        elif k == 'ideas':
            canvas.ask('a new idea', typed(lambda t: s.add_idea(t.strip())))


def run(store, path=None):
    """The canvas, in the terminal, until Esc."""
    # peek: the branch you're on shows what's in it (its notes first) before you go in
    tc.pick('', provider=Desk(store, path), glyphs='plain', colmax=40, peek=True)
