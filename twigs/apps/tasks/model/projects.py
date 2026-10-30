"""Projects with nested tasks, repeating chores, ideas, shelves, and pinned focus."""

from datetime import date

PROJECT_TABLES = ("shelves", "projects", "project_tasks", "ideas", "settings")
# Every sidebar collection can be pinned and shelved.
SIDEBARS = ("books", "habits", "paths", "skills", "projects", "ideas")
REPEATS = ((None, "Doesn't repeat"), (1, "Every day"), (2, "Every 2 days"), (3, "Every 3 days"),
           (7, "Every week"), (14, "Every 2 weeks"), (30, "Every month"))


class ProjectStore:
    def init_projects(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS shelves (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                sidebar_position INTEGER);
            CREATE TABLE IF NOT EXISTS project_tasks (
                id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                parent_id INTEGER REFERENCES project_tasks(id) ON DELETE CASCADE,
                title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0,
                status INTEGER NOT NULL DEFAULT 0 CHECK(status BETWEEN 0 AND 2),
                every_days INTEGER CHECK(every_days IS NULL OR every_days > 0), last_done TEXT);
            CREATE TABLE IF NOT EXISTS ideas (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                sidebar_position INTEGER, created TEXT NOT NULL DEFAULT (date('now','localtime')),
                project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL, taken_day TEXT);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        for table in SIDEBARS:
            columns = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if "pinned" not in columns:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            if "shelf_id" not in columns:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN shelf_id INTEGER REFERENCES shelves(id) ON DELETE SET NULL")
        if "project_task_id" not in {r["name"] for r in self.db.execute("PRAGMA table_info(bullets)")}:
            self.db.execute("ALTER TABLE bullets ADD COLUMN project_task_id INTEGER REFERENCES project_tasks(id) ON DELETE SET NULL")
        if "due" not in {r["name"] for r in self.db.execute("PRAGMA table_info(project_tasks)")}:
            self.db.execute("ALTER TABLE project_tasks ADD COLUMN due TEXT")     # the day it's due, or NULL
        self.db.commit()

    # View settings apply to every sidebar. They are not undo steps, and undo
    # leaves them as they are.
    def view(self):
        values = {r["key"]: r["value"] for r in self.rows("SELECT * FROM settings")}
        shelf = int(values.get("shelf", 0)) or None
        if shelf is not None and not self.rows("SELECT id FROM shelves WHERE id=?", (shelf,)):
            shelf = None
        return {"focus": values.get("focus") == "1", "shelf": shelf}

    def set_view(self, *, focus=None, shelf=None):
        with self.db:
            if focus is not None:
                self.db.execute("INSERT OR REPLACE INTO settings VALUES ('focus',?)", ("1" if focus else "0",))
            if shelf is not None:
                self.db.execute("INSERT OR REPLACE INTO settings VALUES ('shelf',?)", (str(shelf or 0),))

    # Preview lines under sidebar items: idea notes, a project's next step.
    PREVIEWS = ("projects", "ideas")

    def previews(self, table):
        value = self.rows("SELECT value FROM settings WHERE key=?", (f"previews:{table}",))
        return not value or value[0]["value"] == "1"

    def set_previews(self, table, shown):
        if table not in self.PREVIEWS:
            raise ValueError("This list has no previews")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (f"previews:{table}", "1" if shown else "0"))

    def shelves(self):
        return self.rows("SELECT * FROM shelves ORDER BY position,id")

    def add_shelf(self, title):
        with self.change():
            position = self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM shelves").fetchone()[0]
            return self.db.execute("INSERT INTO shelves(title,position) VALUES (?,?)", (title, position)).lastrowid

    def toggle_pin(self, table, item_id):
        if table not in SIDEBARS:
            raise ValueError("Unknown sidebar")
        with self.change():
            self.db.execute(f"UPDATE {table} SET pinned=1-pinned WHERE id=?", (item_id,))
        return bool(self.rows(f"SELECT pinned FROM {table} WHERE id=?", (item_id,))[0]["pinned"])

    def set_shelf(self, table, item_id, shelf_id, new_shelf=None):
        """Shelve an item, optionally on a new shelf, as one undo step."""
        if table not in SIDEBARS:
            raise ValueError("Unknown sidebar")
        with self.change():
            if new_shelf:
                position = self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM shelves").fetchone()[0]
                shelf_id = self.db.execute("INSERT INTO shelves(title,position) VALUES (?,?)", (new_shelf, position)).lastrowid
            self.db.execute(f"UPDATE {table} SET shelf_id=? WHERE id=?", (shelf_id or None, item_id))
        return shelf_id

    def adopt_view(self, table, item_id):
        """New items join the current focus and shelf instead of vanishing from a
        filtered list. Undoing the addition removes both, so this is not its own step."""
        if table not in SIDEBARS:
            raise ValueError("Unknown sidebar")
        view = self.view()
        with self.db:
            self.db.execute(f"UPDATE {table} SET pinned=MAX(pinned,?),shelf_id=COALESCE(?,shelf_id) WHERE id=?",
                            (int(view["focus"]), view["shelf"], item_id))

    def add_project(self, title, notes=""):
        with self.change():
            return self.db.execute("INSERT INTO projects(title,notes) VALUES (?,?)", (title, notes)).lastrowid

    def edit_project(self, project_id, title, notes):
        with self.change():
            self.db.execute("UPDATE projects SET title=?,notes=? WHERE id=?", (title, notes, project_id))

    @staticmethod
    def validate_repeat(every_days):
        if every_days is not None and (not isinstance(every_days, int) or every_days < 1):
            raise ValueError("Repeat interval must be a whole number of days.")

    def set_due(self, task_id, day=None):
        """When a task is due (an ISO day), or not at all (None). One undo step."""
        with self.change():
            self.db.execute("UPDATE project_tasks SET due=? WHERE id=?", (day, task_id))

    def overdue(self, project_id, today):
        """The project's unfinished tasks due before `today` (ISO), as rows."""
        return self.rows("SELECT * FROM project_tasks WHERE project_id=? AND due IS NOT NULL AND due < ? "
                         "AND status != 2 AND every_days IS NULL", (project_id, today))

    def add_task(self, project_id, title, parent_id=None, notes="", every_days=None):
        self.validate_repeat(every_days)
        with self.change():
            position = self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM project_tasks WHERE project_id=? AND parent_id IS ?",
                                       (project_id, parent_id)).fetchone()[0]
            return self.db.execute("INSERT INTO project_tasks(project_id,parent_id,title,notes,position,every_days) VALUES (?,?,?,?,?,?)",
                                   (project_id, parent_id, title, notes, position, every_days)).lastrowid

    def edit_task(self, task_id, title, notes, every_days):
        self.validate_repeat(every_days)
        with self.change():
            self.db.execute("UPDATE project_tasks SET title=?,notes=?,every_days=? WHERE id=?", (title, notes, every_days, task_id))

    def indent_task(self, task_id):
        """Make a step the last subtask of the step above it."""
        task = self.rows("SELECT * FROM project_tasks WHERE id=?", (task_id,))
        if not task:
            return False
        siblings = [r["id"] for r in self.rows("SELECT id FROM project_tasks WHERE project_id=? AND parent_id IS ? ORDER BY position,id",
                                                  (task[0]["project_id"], task[0]["parent_id"]))]
        index = siblings.index(task_id)
        if index == 0:
            return False
        parent = siblings[index - 1]
        with self.change():
            position = self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM project_tasks WHERE parent_id=?", (parent,)).fetchone()[0]
            self.db.execute("UPDATE project_tasks SET parent_id=?,position=? WHERE id=?", (parent, position, task_id))
        return parent

    def outdent_task(self, task_id):
        """Lift a subtask out to sit just below its parent."""
        task = self.rows("SELECT * FROM project_tasks WHERE id=?", (task_id,))
        if not task or task[0]["parent_id"] is None:
            return False
        parent = self.rows("SELECT * FROM project_tasks WHERE id=?", (task[0]["parent_id"],))[0]
        siblings = [r["id"] for r in self.rows("SELECT id FROM project_tasks WHERE project_id=? AND parent_id IS ? ORDER BY position,id",
                                                  (parent["project_id"], parent["parent_id"]))]
        siblings.insert(siblings.index(parent["id"]) + 1, task_id)
        with self.change():
            self.db.execute("UPDATE project_tasks SET parent_id=? WHERE id=?", (parent["parent_id"], task_id))
            self.db.executemany("UPDATE project_tasks SET position=? WHERE id=?", [(n, i) for n, i in enumerate(siblings)])
        return True

    def add_idea(self, title, notes=""):
        with self.change():
            return self.db.execute("INSERT INTO ideas(title,notes,created) VALUES (?,?,?)",
                                   (title, notes, date.today().isoformat())).lastrowid

    def edit_idea(self, idea_id, title, notes):
        with self.change():
            self.db.execute("UPDATE ideas SET title=?,notes=? WHERE id=?", (title, notes, idea_id))

    def take_up_idea(self, idea_id, project_id=None):
        """Start an idea as a new project, or add it as a step to an existing one.
        The idea stays in the list, marked as taken up."""
        idea = self.rows("SELECT * FROM ideas WHERE id=?", (idea_id,))[0]
        with self.change():
            if project_id is None:
                project_id = self.db.execute("INSERT INTO projects(title,notes,pinned,shelf_id) VALUES (?,?,?,?)",
                                             (idea["title"], idea["notes"], idea["pinned"], idea["shelf_id"])).lastrowid
            else:
                position = self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM project_tasks WHERE project_id=? AND parent_id IS NULL",
                                           (project_id,)).fetchone()[0]
                self.db.execute("INSERT INTO project_tasks(project_id,title,notes,position) VALUES (?,?,?,?)",
                                (project_id, idea["title"], idea["notes"], position))
            self.db.execute("UPDATE ideas SET project_id=?,taken_day=? WHERE id=?", (project_id, date.today().isoformat(), idea_id))
        return project_id

    def move_task(self, task_id, direction):
        task = self.rows("SELECT * FROM project_tasks WHERE id=?", (task_id,))
        if not task:
            return False
        siblings = [r["id"] for r in self.rows("SELECT id FROM project_tasks WHERE project_id=? AND parent_id IS ? ORDER BY position,id",
                                                  (task[0]["project_id"], task[0]["parent_id"]))]
        index = siblings.index(task_id)
        if not 0 <= index + direction < len(siblings):
            return False
        siblings[index], siblings[index + direction] = siblings[index + direction], siblings[index]
        with self.change():
            self.db.executemany("UPDATE project_tasks SET position=? WHERE id=?", [(n, i) for n, i in enumerate(siblings)])
        return True

    def project_snapshot(self, project_id, today=None):
        """Tasks, child order, and states. A repeating chore stays done for its interval."""
        today = today or date.today()
        tasks = self.rows("SELECT * FROM project_tasks WHERE project_id=? ORDER BY position,id", (project_id,))
        nodes = {t["id"]: t for t in tasks}
        children = {}
        for task in tasks:
            children.setdefault(task["parent_id"], []).append(task["id"])
        states = {}

        def state(task_id):
            kids = children.get(task_id)
            if kids:
                values = [state(k) for k in kids]
                states[task_id] = 2 if all(v == 2 for v in values) else 1 if any(values) else 0
            else:
                states[task_id] = self.task_state(nodes[task_id], today)
            return states[task_id]

        for task_id in children.get(None, []):
            state(task_id)
        return nodes, children, states

    @staticmethod
    def task_state(task, today):
        if task["every_days"]:
            fresh = task["last_done"] and (today - date.fromisoformat(task["last_done"])).days < task["every_days"]
            return 2 if fresh else 0
        return task["status"]

    def next_task(self, project_id):
        """The first unfinished one-off step, in outline order. Chores are listed separately."""
        nodes, children, states = self.project_snapshot(project_id)

        def search(parent=None):
            for task_id in children.get(parent, []):
                if children.get(task_id):
                    if found := search(task_id):
                        return found
                elif not nodes[task_id]["every_days"] and states[task_id] != 2:
                    return task_id
            return None
        return search()

    def cycle_task(self, task_id, day=None):
        day = day or date.today().isoformat()
        task = self.rows("SELECT * FROM project_tasks WHERE id=?", (task_id,))[0]
        if self.rows("SELECT id FROM project_tasks WHERE parent_id=? LIMIT 1", (task_id,)):
            raise ValueError("This task finishes when its subtasks do. Mark one of them instead.")
        if task["every_days"] and self.task_state(task, date.fromisoformat(day)) == 2:
            return False
        with self.change():
            self._complete_task(task_id, day, cycle=True)
        return True

    def _complete_task(self, task_id, day, cycle=False):
        task = self.rows("SELECT every_days FROM project_tasks WHERE id=?", (task_id,))
        if not task:
            return
        if task[0]["every_days"]:
            self.db.execute("UPDATE project_tasks SET last_done=MAX(COALESCE(last_done,''),?) WHERE id=?", (day, task_id))
        elif cycle:
            self.db.execute("UPDATE project_tasks SET status=(status+1)%3 WHERE id=?", (task_id,))
        else:
            self.db.execute("UPDATE project_tasks SET status=2 WHERE id=?", (task_id,))
