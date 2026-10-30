"""Syllabus persistence, shared resources, progress, and skill evidence."""

import json
from uuid import uuid4

from .syllabus import SyllabusError, flatten_syllabus, validate_syllabus

LEARNING_TABLES = ("paths", "resources", "resource_chapters", "activities", "skills", "skill_criteria")


class LearningStore:
    def init_learning(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS paths (
                id INTEGER PRIMARY KEY, uid TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', mermaid TEXT NOT NULL DEFAULT '',
                ascii_art TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS resources (
                uid TEXT PRIMARY KEY, title TEXT NOT NULL, type TEXT NOT NULL,
                url TEXT NOT NULL DEFAULT '', status INTEGER NOT NULL DEFAULT 0,
                book_id INTEGER REFERENCES books(id) ON DELETE SET NULL);
            CREATE TABLE IF NOT EXISTS resource_chapters (
                resource_uid TEXT NOT NULL REFERENCES resources(uid) ON DELETE CASCADE,
                uid TEXT NOT NULL, chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                PRIMARY KEY(resource_uid,uid));
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY, path_id INTEGER NOT NULL REFERENCES paths(id) ON DELETE CASCADE,
                uid TEXT NOT NULL, parent_id INTEGER REFERENCES activities(id) ON DELETE CASCADE,
                title TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'read',
                position INTEGER NOT NULL DEFAULT 0, optional INTEGER NOT NULL DEFAULT 0,
                status INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
                resource_uid TEXT REFERENCES resources(uid) ON DELETE SET NULL,
                chapter_id INTEGER REFERENCES chapters(id) ON DELETE SET NULL,
                notes TEXT NOT NULL DEFAULT '', mermaid TEXT NOT NULL DEFAULT '',
                ascii_art TEXT NOT NULL DEFAULT '', requires TEXT NOT NULL DEFAULT '[]',
                UNIQUE(path_id,uid));
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY, path_id INTEGER NOT NULL REFERENCES paths(id) ON DELETE CASCADE,
                uid TEXT NOT NULL, title TEXT NOT NULL, level INTEGER NOT NULL DEFAULT 0,
                evidence TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(path_id,uid));
            CREATE TABLE IF NOT EXISTS skill_criteria (
                skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
                PRIMARY KEY(skill_id,activity_id));
        """)
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(bullets)")}
        if "activity_id" not in columns:
            self.db.execute("ALTER TABLE bullets ADD COLUMN activity_id INTEGER REFERENCES activities(id) ON DELETE SET NULL")
        for table in ("paths", "activities"):
            columns = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if "ascii_piece" not in columns:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN ascii_piece TEXT NOT NULL DEFAULT ''")
        self.db.commit()

    def import_preview(self, data):
        validate_syllabus(data)
        existing = self.rows("SELECT id FROM paths WHERE uid=?", (data["id"],))
        nodes = flatten_syllabus(data)
        old = self.rows("SELECT uid FROM activities WHERE path_id=? AND active=1", (existing[0]["id"],)) if existing else []
        old_ids, new_ids = {r["uid"] for r in old}, {r["id"] for r in nodes}
        return {"title": data["title"], "id": data["id"], "action": "Update" if existing else "Create",
                "activities": len(nodes), "new": len(new_ids-old_ids), "retained": len(new_ids & old_ids),
                "archived": len(old_ids-new_ids), "resources": len(data.get("resources", [])),
                "skills": len(data.get("skills", []))}

    def import_syllabus(self, data):
        validate_syllabus(data)
        # Stable resource IDs are global. Their type must not change under users'
        # existing progress, even when another syllabus references the same source.
        for resource in data.get("resources", []):
            existing = self.rows("SELECT type FROM resources WHERE uid=?", (resource["id"],))
            if existing and existing[0]["type"] != resource.get("type", "other"):
                raise SyllabusError(f'Resource type changed for {resource["id"]}; use a new ID.')
        nodes = flatten_syllabus(data)
        with self.change():
            self.db.execute("""INSERT INTO paths(uid,title,description,mermaid,ascii_art) VALUES (?,?,?,?,?)
                ON CONFLICT(uid) DO UPDATE SET title=excluded.title,description=excluded.description,
                mermaid=excluded.mermaid,ascii_art=excluded.ascii_art""",
                (data["id"], data["title"], data.get("description", ""), data.get("mermaid", ""), data.get("ascii", "")))
            path_id = self.rows("SELECT id FROM paths WHERE uid=?", (data["id"],))[0]["id"]
            self.db.execute("UPDATE paths SET ascii_piece=? WHERE id=?", (data.get("ascii_piece", ""), path_id))
            for resource in data.get("resources", []):
                uid = resource["id"]
                self.db.execute("""INSERT INTO resources(uid,title,type,url) VALUES (?,?,?,?)
                    ON CONFLICT(uid) DO UPDATE SET title=excluded.title,url=excluded.url""",
                    (uid, resource["title"], resource.get("type", "other"), resource.get("url", "")))
                if resource.get("type") == "book":
                    book_id = self.rows("SELECT book_id FROM resources WHERE uid=?", (uid,))[0]["book_id"]
                    if book_id is None:
                        book_id = self.db.execute("INSERT INTO books(title,textbook) VALUES (?,?)", (resource["title"], resource.get("textbook", False))).lastrowid
                        self.db.execute("UPDATE resources SET book_id=? WHERE uid=?", (book_id, uid))
                    else:
                        self.db.execute("UPDATE books SET title=?,textbook=? WHERE id=?", (resource["title"], resource.get("textbook", False), book_id))
                    for position, chapter in enumerate(resource.get("chapters", [])):
                        matches = self.rows("SELECT chapter_id FROM resource_chapters WHERE resource_uid=? AND uid=?", (uid, chapter["id"]))
                        if matches:
                            self.db.execute("UPDATE chapters SET title=?,position=? WHERE id=?", (chapter["title"], position, matches[0]["chapter_id"]))
                        else:
                            chapter_id = self.db.execute("INSERT INTO chapters(book_id,title,position) VALUES (?,?,?)", (book_id, chapter["title"], position)).lastrowid
                            self.db.execute("INSERT INTO resource_chapters VALUES (?,?,?)", (uid, chapter["id"], chapter_id))
            self.db.execute("UPDATE activities SET active=0 WHERE path_id=?", (path_id,))
            for node in nodes:
                resource = node.get("resource")
                chapter = self.rows("SELECT chapter_id FROM resource_chapters WHERE resource_uid=? AND uid=?", (resource, node["chapter"]))[0]["chapter_id"] if node.get("chapter") else None
                parent = self.rows("SELECT id FROM activities WHERE path_id=? AND uid=?", (path_id, node["parent"]))[0]["id"] if node["parent"] else None
                existing = self.rows("SELECT kind,resource_uid,chapter_id FROM activities WHERE path_id=? AND uid=?", (path_id, node["id"]))
                kind = node.get("kind", "module" if node.get("children") else "read")
                if existing and (existing[0]["kind"], existing[0]["resource_uid"], existing[0]["chapter_id"]) != (kind, resource, chapter):
                    raise SyllabusError(f'Activity {node["id"]} changed its meaning; assign a new stable ID.')
                self.db.execute("""INSERT INTO activities(path_id,uid,parent_id,title,kind,position,optional,
                    resource_uid,chapter_id,notes,mermaid,ascii_art,requires) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(path_id,uid) DO UPDATE SET parent_id=excluded.parent_id,title=excluded.title,
                    position=excluded.position,optional=excluded.optional,notes=excluded.notes,
                    mermaid=excluded.mermaid,ascii_art=excluded.ascii_art,requires=excluded.requires,active=1""",
                    (path_id, node["id"], parent, node["title"], kind, node["position"], node.get("optional", False),
                     resource, chapter, node.get("notes", ""), node.get("mermaid", ""), node.get("ascii", ""), json.dumps(node.get("requires", []))))
                self.db.execute("UPDATE activities SET ascii_piece=? WHERE path_id=? AND uid=?", (node.get("ascii_piece", ""), path_id, node["id"]))
            self.db.execute("UPDATE skills SET active=0 WHERE path_id=?", (path_id,))
            for skill in data.get("skills", []):
                self.db.execute("""INSERT INTO skills(path_id,uid,title) VALUES (?,?,?)
                    ON CONFLICT(path_id,uid) DO UPDATE SET title=excluded.title,active=1""", (path_id, skill["id"], skill["title"]))
                skill_id = self.rows("SELECT id FROM skills WHERE path_id=? AND uid=?", (path_id, skill["id"]))[0]["id"]
                self.db.execute("DELETE FROM skill_criteria WHERE skill_id=?", (skill_id,))
                self.db.executemany("INSERT OR IGNORE INTO skill_criteria VALUES (?,?)",
                    [(skill_id, self.rows("SELECT id FROM activities WHERE path_id=? AND uid=?", (path_id, uid))[0]["id"]) for uid in skill.get("criteria", [])])
        return path_id

    def learning_snapshot(self, path_id):
        nodes = self.rows("SELECT * FROM activities WHERE path_id=? AND active=1 ORDER BY position,id", (path_id,))
        by_id = {n["id"]: n for n in nodes}
        children = {None: []}
        for node in nodes:
            children.setdefault(node["parent_id"], []).append(node["id"])
        chapters = {c["id"]: c for c in self.rows("SELECT * FROM chapters")}
        resources = {r["uid"]: r for r in self.rows("SELECT * FROM resources")}
        states = {}
        def aggregate(values):
            return 2 if values and all(v == 2 for v in values) else 1 if any(values) else 0
        def state(node_id):
            if node_id in states:
                return states[node_id]
            node = by_id[node_id]
            descendants = children.get(node_id, [])
            if descendants:
                required = [i for i in descendants if not by_id[i]["optional"]]
                result = aggregate([state(i) for i in required or descendants])
            elif node["chapter_id"] in chapters:
                chapter = chapters[node["chapter_id"]]
                result = chapter["exercises"] * 2 if node["kind"] == "exercise" else chapter["reading"]
            elif node["resource_uid"] in resources and node["kind"] in {"read", "video", "article"}:
                resource = resources[node["resource_uid"]]
                book_chapters = [c["reading"] for c in chapters.values() if c["book_id"] == resource["book_id"]] if resource["book_id"] else []
                result = aggregate(book_chapters) if book_chapters else resource["status"]
            else:
                result = node["status"]
            states[node_id] = result
            return result
        for node_id in by_id:
            state(node_id)
        return by_id, children, states

    def unmet_prerequisites(self, path_id, node_id):
        """Titles of what this activity, or a module it is in, suggests finishing first and is not finished."""
        nodes, _, states = self.learning_snapshot(path_id)
        by_uid = {n["uid"]: n for n in nodes.values()}
        missing = []
        current = nodes.get(node_id)
        while current:
            for uid in json.loads(current["requires"]):
                if uid in by_uid and states[by_uid[uid]["id"]] != 2:
                    missing.append(by_uid[uid]["title"])
            current = nodes.get(current["parent_id"])
        return list(dict.fromkeys(missing))

    def next_activity(self, path_id):
        """Continue: the first unfinished required activity, in outline order, whose suggested
        prerequisites are complete. None when nothing is ready."""
        nodes, children, states = self.learning_snapshot(path_id)

        def search(parent=None):
            for node_id in children.get(parent, []):
                if nodes[node_id]["optional"]:
                    continue
                if children.get(node_id):
                    found = search(node_id)
                    if found:
                        return found
                elif states[node_id] != 2 and not self.unmet_prerequisites(path_id, node_id):
                    return node_id
            return None
        return search()

    def _set_activity(self, activity_id, state):
        node = self.rows("SELECT * FROM activities WHERE id=?", (activity_id,))[0]
        if self.rows("SELECT id FROM activities WHERE parent_id=? AND active=1", (activity_id,)):
            raise ValueError("Complete the activities inside this module individually.")
        if node["chapter_id"]:
            column = "exercises" if node["kind"] == "exercise" else "reading"
            self.db.execute(f"UPDATE chapters SET {column}=? WHERE id=?", (int(state == 2) if column == "exercises" else state, node["chapter_id"]))
        elif node["resource_uid"] and node["kind"] in {"read", "video", "article"}:
            resource = self.rows("SELECT * FROM resources WHERE uid=?", (node["resource_uid"],))[0]
            if resource["book_id"] and self.rows("SELECT id FROM chapters WHERE book_id=?", (resource["book_id"],)):
                raise ValueError("Track this book's chapters in the Library, or add chapter activities.")
            self.db.execute("UPDATE resources SET status=? WHERE uid=?", (state, node["resource_uid"]))
        self.db.execute("UPDATE activities SET status=? WHERE id=?", (state, activity_id))

    def cycle_activity(self, activity_id):
        node = self.rows("SELECT path_id,kind,chapter_id FROM activities WHERE id=?", (activity_id,))[0]
        _, _, states = self.learning_snapshot(node["path_id"])
        with self.change():
            state = (0 if states[activity_id] == 2 else 2) if node["kind"] == "exercise" and node["chapter_id"] else (states[activity_id]+1) % 3
            self._set_activity(activity_id, state)

    def add_path(self, title, description=""):
        return self.import_syllabus({"version": 1, "id": f"local:{uuid4().hex}", "title": title, "description": description, "modules": []})

    def add_activity(self, path_id, title, kind="read", parent_id=None, notes="", optional=False, chapter_id=None, url=""):
        with self.change():
            resource_uid = None
            if chapter_id:
                chapter = self.rows("SELECT book_id FROM chapters WHERE id=?", (chapter_id,))[0]
                book = self.rows("SELECT * FROM books WHERE id=?", (chapter["book_id"],))[0]
                resources = self.rows("SELECT uid FROM resources WHERE book_id=?", (book["id"],))
                if resources:
                    resource_uid = resources[0]["uid"]
                else:
                    resource_uid = f"local:book:{uuid4().hex}"
                    self.db.execute("INSERT INTO resources(uid,title,type,book_id) VALUES (?,?,'book',?)", (resource_uid, book["title"], book["id"]))
                for unit in self.rows("SELECT id FROM chapters WHERE book_id=?", (book["id"],)):
                    if not self.rows("SELECT uid FROM resource_chapters WHERE resource_uid=? AND chapter_id=?", (resource_uid, unit["id"])):
                        self.db.execute("INSERT INTO resource_chapters VALUES (?,?,?)", (resource_uid, f"local:{uuid4().hex}", unit["id"]))
            elif url:
                from urllib.parse import urlsplit
                if urlsplit(url).scheme not in {"https", "http"} or not urlsplit(url).netloc:
                    raise ValueError("Resource links must use http or https.")
                resource_uid = f"local:resource:{uuid4().hex}"
                resource_type = kind if kind in {"video", "article"} else "other"
                self.db.execute("INSERT INTO resources(uid,title,type,url) VALUES (?,?,?,?)", (resource_uid, title, resource_type, url))
            position = self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM activities WHERE path_id=? AND parent_id IS ?", (path_id, parent_id)).fetchone()[0]
            return self.db.execute("INSERT INTO activities(path_id,uid,parent_id,title,kind,position,notes,optional,chapter_id,resource_uid) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (path_id, f"local:{uuid4().hex}", parent_id, title, kind, position, notes, optional, chapter_id, resource_uid)).lastrowid

    def save_skill(self, skill_id, level, evidence):
        if level not in {0, 1, 2, 3}:
            raise ValueError("Unknown skill level")
        if level == 3 and not evidence.strip():
            raise ValueError("Record evidence before marking a skill demonstrated.")
        with self.change():
            self.db.execute("UPDATE skills SET level=?,evidence=? WHERE id=?", (level, evidence, skill_id))

    def add_skill(self, path_id, title):
        with self.change():
            return self.db.execute("INSERT INTO skills(path_id,uid,title) VALUES (?,?,?)", (path_id, f"local:{uuid4().hex}", title)).lastrowid

    def archive_activity(self, activity_id):
        with self.change():
            self.db.execute("""WITH RECURSIVE subtree(id) AS (
                SELECT id FROM activities WHERE id=? UNION ALL SELECT a.id FROM activities a JOIN subtree s ON a.parent_id=s.id)
                UPDATE activities SET active=0 WHERE id IN (SELECT id FROM subtree)""", (activity_id,))

    def export_syllabus(self, path_id):
        path = self.rows("SELECT * FROM paths WHERE id=?", (path_id,))[0]
        nodes, children, _ = self.learning_snapshot(path_id)
        resource_ids = {n["resource_uid"] for n in nodes.values() if n["resource_uid"]}
        resources = []
        for uid in sorted(resource_ids):
            resource = self.rows("SELECT * FROM resources WHERE uid=?", (uid,))[0]
            entry = {"id": uid, "title": resource["title"], "type": resource["type"], "url": resource["url"]}
            if resource["book_id"]:
                entry["textbook"] = bool(self.rows("SELECT textbook FROM books WHERE id=?", (resource["book_id"],))[0]["textbook"])
                entry["chapters"] = [{"id": c["uid"], "title": c["title"]} for c in self.rows(
                    "SELECT rc.uid,c.title FROM resource_chapters rc JOIN chapters c ON c.id=rc.chapter_id WHERE rc.resource_uid=? ORDER BY c.position,c.id", (uid,))]
            resources.append(entry)
        def serialize(node_id):
            node = nodes[node_id]
            entry = {"id": node["uid"], "title": node["title"], "kind": node["kind"],
                     "optional": bool(node["optional"]), "notes": node["notes"], "requires": json.loads(node["requires"]),
                     "mermaid": node["mermaid"], "ascii": node["ascii_art"], "ascii_piece": node["ascii_piece"]}
            if node["resource_uid"]:
                entry["resource"] = node["resource_uid"]
            if node["chapter_id"]:
                match = self.rows("SELECT uid FROM resource_chapters WHERE resource_uid=? AND chapter_id=?", (node["resource_uid"], node["chapter_id"]))
                if match:
                    entry["chapter"] = match[0]["uid"]
            if children.get(node_id):
                entry["children"] = [serialize(child) for child in children[node_id]]
            return entry
        skills = []
        for skill in self.rows("SELECT * FROM skills WHERE path_id=? AND active=1 ORDER BY id", (path_id,)):
            skills.append({"id": skill["uid"], "title": skill["title"], "criteria": [c["uid"] for c in self.rows(
                "SELECT a.uid FROM skill_criteria sc JOIN activities a ON a.id=sc.activity_id WHERE sc.skill_id=? AND a.active=1 ORDER BY a.id", (skill["id"],))]})
        return {"version": 1, "id": path["uid"], "title": path["title"], "description": path["description"],
                "mermaid": path["mermaid"], "ascii": path["ascii_art"], "ascii_piece": path["ascii_piece"], "resources": resources,
                "modules": [serialize(i) for i in children.get(None, [])], "skills": skills}
