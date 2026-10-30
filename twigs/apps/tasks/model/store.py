"""Local persistence. Each user action is one transaction and one undo step."""

from contextlib import contextmanager
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3

from .learning import LEARNING_TABLES, LearningStore
from .projects import PROJECT_TABLES, SIDEBARS, ProjectStore


class Store(ProjectStore, LearningStore):
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL,
                textbook INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                title TEXT NOT NULL, position INTEGER NOT NULL,
                reading INTEGER NOT NULL DEFAULT 0 CHECK(reading BETWEEN 0 AND 2),
                exercises INTEGER NOT NULL DEFAULT 0 CHECK(exercises IN (0,1)));
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, unit TEXT NOT NULL DEFAULT 'sessions',
                daily_goal INTEGER NOT NULL DEFAULT 1 CHECK(daily_goal > 0),
                weekly_goal INTEGER NOT NULL DEFAULT 7 CHECK(weekly_goal BETWEEN 1 AND 7));
            CREATE TABLE IF NOT EXISTS checkins (
                habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
                day TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount >= 0),
                PRIMARY KEY(habit_id, day));
            CREATE TABLE IF NOT EXISTS bullets (
                id INTEGER PRIMARY KEY, day TEXT NOT NULL, text TEXT NOT NULL,
                note INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0,
                migrated INTEGER NOT NULL DEFAULT 0, credited INTEGER NOT NULL DEFAULT 0,
                chapter_id INTEGER REFERENCES chapters(id) ON DELETE SET NULL,
                habit_id INTEGER REFERENCES habits(id) ON DELETE SET NULL);
        """)
        self.init_learning()
        self.init_projects()
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(habits)")}
        if "goal_period" not in columns:
            self.db.execute("ALTER TABLE habits ADD COLUMN goal_period TEXT NOT NULL DEFAULT 'day' CHECK(goal_period IN ('day','week'))")
        if "parent_id" not in columns:
            self.db.execute("ALTER TABLE habits ADD COLUMN parent_id INTEGER REFERENCES habits(id) ON DELETE SET NULL")
        if "planned" not in {r["name"] for r in self.db.execute("PRAGMA table_info(checkins)")}:
            self.db.execute("ALTER TABLE checkins ADD COLUMN planned INTEGER NOT NULL DEFAULT 0 CHECK(planned IN (0,1))")
        for table in SIDEBARS:
            if "sidebar_position" not in {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN sidebar_position INTEGER")
        self.db.execute("CREATE TABLE IF NOT EXISTS sidebar_preferences (collection TEXT PRIMARY KEY, sort TEXT NOT NULL)")
        self.db.commit()
        self._undo: list[bytes] = []

    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    @staticmethod
    def sidebar_sorts(table):
        if table not in SIDEBARS:
            raise ValueError("Unknown sidebar")
        return ["custom", "name", "name-desc", "oldest", "newest"] + (["level"] if table == "skills" else [])

    def sidebar_sort(self, table):
        self.sidebar_sorts(table)
        preference = self.rows("SELECT sort FROM sidebar_preferences WHERE collection=?", (table,))
        return preference[0]["sort"] if preference else "level" if table == "skills" else "custom"

    def set_sidebar_sort(self, table, sort):
        if sort not in self.sidebar_sorts(table):
            raise ValueError("Unknown sort option")
        if sort != self.sidebar_sort(table):
            with self.change():
                self._set_sidebar_sort(table, sort)

    def _set_sidebar_sort(self, table, sort):
        self.db.execute("INSERT INTO sidebar_preferences VALUES (?,?) ON CONFLICT(collection) DO UPDATE SET sort=excluded.sort", (table, sort))

    def sidebar_items(self, table, filtered=True):
        """Sorted items, narrowed to the pinned focus and chosen shelf when filtered."""
        sort = self.sidebar_sort(table)
        items = self.rows(f"SELECT * FROM {table}" + (" WHERE active=1" if table == "skills" else ""))
        if filtered:
            view = self.view()
            items = [row for row in items if (row["pinned"] or not view["focus"])
                     and (view["shelf"] is None or row["shelf_id"] == view["shelf"])]
        keys = {
            "custom": lambda row: (row["sidebar_position"] is None, row["sidebar_position"] or 0, row["id"]),
            "name": lambda row: (row["title"].casefold(), row["id"]),
            "name-desc": lambda row: (row["title"].casefold(), row["id"]),
            "oldest": lambda row: row["id"], "newest": lambda row: -row["id"],
            "level": lambda row: (-row["level"], row["id"]),
        }
        items.sort(key=keys[sort], reverse=sort == "name-desc")
        if table != "habits":
            return items
        # A child whose parent is filtered out shows at the top level.
        shown = {item["id"] for item in items}
        children = {}
        for item in items:
            children.setdefault(item["parent_id"] if item["parent_id"] in shown else None, []).append(item)
        ordered = []
        def walk(parent, depth):
            for item in children.get(parent, []):
                ordered.append({**item, "sidebar_depth": depth, "sidebar_parent": parent})
                walk(item["id"], depth + 1)
        walk(None, 0)
        return ordered

    def move_sidebar_item(self, table, item_id, direction):
        """Move among siblings, keeping groups intact; adopt the visible order."""
        if direction not in {-1, 1}:
            raise ValueError("Move up or down")
        items = self.sidebar_items(table)
        item = next((row for row in items if row["id"] == item_id), None)
        if item is None:
            return False
        siblings = [row for row in items if table != "habits" or row["sidebar_parent"] == item["sidebar_parent"]]
        index = next(i for i, row in enumerate(siblings) if row["id"] == item_id)
        if not 0 <= index + direction < len(siblings):
            return False
        # Swap with the visible neighbour, keeping hidden items' places.
        positions = {row["id"]: i for i, row in enumerate(self.sidebar_items(table, filtered=False))}
        neighbor = siblings[index + direction]["id"]
        positions[item_id], positions[neighbor] = positions[neighbor], positions[item_id]
        with self.change():
            self.db.executemany(f"UPDATE {table} SET sidebar_position=? WHERE id=?", [(position, row_id) for row_id, position in positions.items()])
            self._set_sidebar_sort(table, "custom")
        return True

    @contextmanager
    def change(self):
        snapshot = self.db.serialize()
        with self.db:
            yield
        self._undo.append(snapshot)
        self._undo = self._undo[-50:]

    def undo(self):
        if not self._undo:
            return False
        # Backup writes restored pages to the actual file; deserialize alone would
        # detach this connection from its on-disk database.
        settings = self.rows("SELECT * FROM settings")
        restored = sqlite3.connect(":memory:")
        try:
            restored.deserialize(self._undo.pop())
            restored.backup(self.db)
        finally:
            restored.close()
        with self.db:
            self.db.execute("DELETE FROM settings")
            self.db.executemany("INSERT INTO settings VALUES (?,?)", [(r["key"], r["value"]) for r in settings])
        return True

    def add_book(self, title, chapters, textbook=False):
        with self.change():
            book_id = self.db.execute("INSERT INTO books(title,textbook) VALUES (?,?)", (title, textbook)).lastrowid
            self.db.executemany("INSERT INTO chapters(book_id,title,position) VALUES (?,?,?)",
                                [(book_id, t, n) for n, t in enumerate(chapters)])
        return book_id

    def add_chapter(self, book_id, title):
        with self.change():
            position = self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM chapters WHERE book_id=?", (book_id,)).fetchone()[0]
            self.db.execute("INSERT INTO chapters(book_id,title,position) VALUES (?,?,?)", (book_id, title, position))

    def cycle_chapter(self, chapter_id, exercises=False):
        with self.change():
            if exercises:
                self.db.execute("UPDATE chapters SET exercises=1-exercises WHERE id=?", (chapter_id,))
            else:
                self.db.execute("UPDATE chapters SET reading=(reading+1)%3 WHERE id=?", (chapter_id,))

    def validate_habit(self, unit, daily_goal, weekly_goal, goal_period, parent_id=None, habit_id=None):
        if not isinstance(daily_goal, int) or not 1 <= daily_goal <= 1000000:
            raise ValueError("Goal must be a whole number from 1 to 1000000.")
        if goal_period not in {"day", "week"} or not 1 <= weekly_goal <= 7:
            raise ValueError("Choose a daily or weekly goal; days per week must be from 1 to 7.")
        habits = {h["id"]: h for h in self.rows("SELECT * FROM habits")}
        seen = {habit_id}
        ancestor = parent_id
        while ancestor is not None:
            if ancestor in seen:
                raise ValueError("A habit cannot be its own ancestor.")
            if ancestor not in habits:
                raise ValueError("Choose an existing parent habit.")
            seen.add(ancestor)
            if habits[ancestor]["unit"] != unit:
                raise ValueError("Parent and child habits must use the same unit.")
            ancestor = habits[ancestor]["parent_id"]
        if habit_id is not None and any(h["parent_id"] == habit_id and h["unit"] != unit for h in habits.values()):
            raise ValueError("Parent and child habits must use the same unit.")

    def add_habit(self, title, unit="sessions", daily_goal=1, weekly_goal=7, goal_period="day", parent_id=None):
        self.validate_habit(unit, daily_goal, weekly_goal, goal_period, parent_id)
        with self.change():
            return self.db.execute("INSERT INTO habits(title,unit,daily_goal,weekly_goal,goal_period,parent_id) VALUES (?,?,?,?,?,?)",
                                   (title, unit, daily_goal, weekly_goal, goal_period, parent_id)).lastrowid

    def edit_habit(self, habit_id, title, unit, daily_goal, weekly_goal, goal_period="day", parent_id=None):
        self.validate_habit(unit, daily_goal, weekly_goal, goal_period, parent_id, habit_id)
        with self.change():
            self.db.execute("UPDATE habits SET title=?,unit=?,daily_goal=?,weekly_goal=?,goal_period=?,parent_id=? WHERE id=?",
                            (title, unit, daily_goal, weekly_goal, goal_period, parent_id, habit_id))

    def habit_counts(self, habit_id, *, planned=None):
        """Sum direct entries and descendants without copying or double crediting."""
        return {r["day"]: r["amount"] for r in self.rows("""
            WITH RECURSIVE family(id) AS (
                SELECT id FROM habits WHERE id=? UNION ALL
                SELECT h.id FROM habits h JOIN family f ON h.parent_id=f.id)
            SELECT day,SUM(amount) AS amount FROM checkins
            WHERE habit_id IN (SELECT id FROM family) AND (? IS NULL OR planned=?) GROUP BY day
        """, (habit_id, planned, planned))}

    def set_checkin(self, habit_id, day, amount):
        date.fromisoformat(day)
        if not isinstance(amount, int) or amount < 0:
            raise ValueError("Amount must be a nonnegative whole number.")
        with self.change():
            self.db.execute("INSERT INTO checkins(habit_id,day,amount,planned) VALUES (?,?,?,?) ON CONFLICT(habit_id,day) DO UPDATE SET amount=excluded.amount,planned=excluded.planned",
                            (habit_id, day, amount, day > date.today().isoformat()))

    def _increment(self, habit_id, day, amount):
        if amount >= 0:
            self.db.execute("""INSERT INTO checkins(habit_id,day,amount,planned) VALUES (?,?,?,?) ON CONFLICT(habit_id,day)
                            DO UPDATE SET amount=amount+excluded.amount,planned=excluded.planned""",
                            (habit_id, day, amount, day > date.today().isoformat()))
        else:
            self.db.execute("UPDATE checkins SET amount=MAX(0,amount+?),planned=? WHERE habit_id=? AND day=?",
                            (amount, day > date.today().isoformat(), habit_id, day))

    def checkin(self, habit_id, day, amount):
        with self.change():
            self._increment(habit_id, day, amount)

    def add_bullet(self, day, text, chapter_id=None, habit_id=None, activity_id=None, project_task_id=None):
        note = text.startswith("- ")
        with self.change():
            return self.db.execute("INSERT INTO bullets(day,text,note,chapter_id,habit_id,activity_id,project_task_id) VALUES (?,?,?,?,?,?,?)",
                                   (day, text[2:] if note else text, note, chapter_id, habit_id, activity_id, project_task_id)).lastrowid

    def toggle_bullet(self, bullet_id):
        bullet = self.rows("SELECT * FROM bullets WHERE id=?", (bullet_id,))[0]
        if bullet["note"] or bullet["migrated"]:
            return
        with self.change():
            self.db.execute("UPDATE bullets SET done=1-done WHERE id=?", (bullet_id,))
            # Reopening a task does not erase independent progress. Undo reverses
            # the complete linked action if it was accidental.
            if not bullet["done"] and not bullet["credited"]:
                if bullet["activity_id"]:
                    self._set_activity(bullet["activity_id"], 2)
                if bullet["chapter_id"]:
                    self.db.execute("UPDATE chapters SET reading=2 WHERE id=?", (bullet["chapter_id"],))
                if bullet["habit_id"]:
                    self._increment(bullet["habit_id"], bullet["day"], 1)
                if bullet["project_task_id"]:
                    self._complete_task(bullet["project_task_id"], bullet["day"])
                self.db.execute("UPDATE bullets SET credited=1 WHERE id=?", (bullet_id,))

    def migrate(self, bullet_id, target_day):
        bullet = self.rows("SELECT * FROM bullets WHERE id=?", (bullet_id,))[0]
        if bullet["done"] or bullet["note"] or bullet["migrated"] or bullet["day"] == target_day:
            return False
        with self.change():
            self.db.execute("UPDATE bullets SET migrated=1 WHERE id=?", (bullet_id,))
            self.db.execute("INSERT INTO bullets(day,text,chapter_id,habit_id,activity_id,project_task_id,credited) VALUES (?,?,?,?,?,?,?)",
                            (target_day, bullet["text"], bullet["chapter_id"], bullet["habit_id"], bullet["activity_id"],
                             bullet["project_task_id"], bullet["credited"]))
        return True

    def delete(self, table, item_id):
        if table not in {"books", "chapters", "habits", "bullets", "paths", "skills", "projects", "project_tasks", "shelves", "ideas"}:
            raise ValueError("Unknown collection")
        with self.change():
            self.db.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))

    def rename(self, table, item_id, title):
        if table not in {"books", "chapters", "habits", "bullets", "paths", "activities", "skills", "projects", "project_tasks", "shelves", "ideas"}:
            raise ValueError("Unknown collection")
        column = "text" if table == "bullets" else "title"
        with self.change():
            self.db.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (title, item_id))
            if table == "books":
                self.db.execute("UPDATE resources SET title=? WHERE book_id=?", (title, item_id))

    def export(self):
        return json.dumps({"version": 3, **{t: self.rows(f"SELECT * FROM {t}") for t in
                          ("books", "chapters", "habits", "checkins", "bullets", "sidebar_preferences", *LEARNING_TABLES, *PROJECT_TABLES)}},
                          indent=2, ensure_ascii=False)

    def seed_demo(self):
        book = self.add_book("Linear Algebra", ["Systems of equations", "Row reduction", "Vector spaces", "Linear independence", "Bases and dimension", "Linear maps", "Eigenvalues", "Inner products"], True)
        self.add_book("A Room of One’s Own", [f"Chapter {n}" for n in range(1, 7)])
        self.add_book("The Craft of Research", ["Thinking in print", "Connecting with your reader", "From topics to questions", "From questions to a problem", "Finding sources"])
        ids = self.rows("SELECT id FROM chapters WHERE book_id=? ORDER BY position", (book,))
        with self.db:
            for chapter in ids[:3]:
                self.db.execute("UPDATE chapters SET reading=2,exercises=1 WHERE id=?", (chapter["id"],))
            self.db.execute("UPDATE chapters SET reading=1 WHERE id=?", (ids[3]["id"],))
        reading = self.add_habit("Read", "sessions", 1, 5)
        exercise = self.add_habit("Exercise", "minutes", 30, 3)
        self.add_habit("Language practice", "minutes", 15, 5)
        today = date.today()
        for n in range(1, 85):
            day = (today - timedelta(days=n)).isoformat()
            if n % 5 != 0:
                self.checkin(reading, day, 1)
            if n % 3 == 0:
                self.checkin(exercise, day, 30)
        self.add_bullet(today.isoformat(), "Read: Linear independence", ids[3]["id"], reading)
        self.add_bullet(today.isoformat(), "Email supervisor")
        self.add_bullet(today.isoformat(), "- Try the exercises before checking solutions")
        self.add_bullet((today - timedelta(days=1)).isoformat(), "Pick up library holds")
        from .demo import DEMO_SYLLABUS
        self.import_syllabus(DEMO_SYLLABUS)
        home = self.add_shelf("Home")
        chores = self.add_project("Home", "An ongoing area: chores come back round, nothing is overdue.")
        self.set_shelf("projects", chores, home)
        laundry = self.add_task(chores, "Laundry", every_days=7)
        self.add_task(chores, "Water the plants", every_days=3)
        self.add_task(chores, "Change the bed sheets", every_days=14)
        self.add_task(chores, "Fix the wobbly shelf")
        thesis = self.add_project("Thesis chapter 2", "Done means a draft my supervisor can read.")
        outline = self.add_task(thesis, "Outline")
        for title in ("List the three main arguments", "Find one source per argument", "Order the sections"):
            self.add_task(thesis, title, outline)
        self.add_task(thesis, "Write the messy first draft")
        self.add_task(thesis, "Send to supervisor")
        self.toggle_pin("projects", thesis)
        self.add_idea("Learn to bake sourdough", "Saw a starter guide. Maybe in winter.")
        self.add_idea("Map the family's Kharkiv addresses", "Could be a nice small website.")
        with self.db:
            self.db.execute("UPDATE project_tasks SET last_done=? WHERE id=?", ((today - timedelta(days=9)).isoformat(), laundry))
            self.db.execute("UPDATE project_tasks SET status=2 WHERE project_id=? AND title='List the three main arguments'", (thesis,))
        self._undo.clear()

    def close(self):
        self.db.close()
