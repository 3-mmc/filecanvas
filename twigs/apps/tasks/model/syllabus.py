"""Agent-facing syllabus format. Validate before touching the journal."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

KINDS = {"module", "read", "video", "article", "exercise", "project", "note"}
RESOURCE_TYPES = {"book", "video", "article", "course", "other"}


class SyllabusError(ValueError):
    pass


def load_syllabus(path):
    path = Path(path).expanduser()
    if path.stat().st_size > 2_000_000:
        raise SyllabusError("Syllabus files must be smaller than 2 MB.")
    try:
        return validate_syllabus(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise SyllabusError(f"Invalid syllabus JSON: {exc}") from exc


def validate_syllabus(data):
    def fail(message):
        raise SyllabusError(message)

    def text(obj, key, *, required=False, limit=20000):
        value = obj.get(key, "")
        if not isinstance(value, str) or len(value) > limit:
            fail(f"{key} must be text of at most {limit} characters.")
        if required and not value.strip():
            fail(f"Missing {key}.")
        if any(ord(c) < 32 and c not in "\n\t" for c in value):
            fail(f"Control characters are not allowed in {key}.")
        return value

    def identity(obj):
        uid = text(obj, "id", required=True, limit=160)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", uid):
            fail(f"Invalid stable ID: {uid!r}.")
        text(obj, "title", required=True, limit=500)
        return uid

    def objects(obj, key):
        values = obj.get(key, [])
        if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
            fail(f"{key} must be a list of objects.")
        if len(values) > 2000:
            fail(f"Too many entries in {key}.")
        return values

    def art_piece(obj):
        piece = text(obj, "ascii_piece", limit=100)
        if piece and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", piece):
            fail("ascii_piece must be an ASCII Magic piece slug, such as seed-germination.")

    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        fail("Expected a syllabus object with version: 1.")
    if "modules" not in data:
        fail("A syllabus must include a modules list (it may be empty).")
    identity(data)
    for key in ("description", "mermaid", "ascii"):
        text(data, key)
    art_piece(data)
    resources = {}
    for resource in objects(data, "resources"):
        uid = identity(resource)
        if uid in resources:
            fail(f"Duplicate resource ID: {uid}.")
        if not isinstance(resource.get("type", "other"), str) or resource.get("type", "other") not in RESOURCE_TYPES:
            fail(f"Unknown resource type for {uid}.")
        url = text(resource, "url", limit=4000)
        if url and (urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc):
            fail(f"Resource URLs must use http or https: {uid}.")
        if "textbook" in resource and type(resource["textbook"]) is not bool:
            fail("textbook must be true or false.")
        chapters = objects(resource, "chapters")
        if chapters and resource.get("type") != "book":
            fail(f"Only books can contain chapters: {uid}.")
        seen = set()
        for chapter in chapters:
            chapter_id = identity(chapter)
            if chapter_id in seen:
                fail(f"Duplicate chapter ID in {uid}: {chapter_id}.")
            seen.add(chapter_id)
        resources[uid] = seen

    nodes = {}
    ancestors = {}
    def walk(children, parents=()):
        if len(parents) > 12:
            fail("Syllabi may be nested at most 12 levels deep.")
        for node in children:
            uid = identity(node)
            if uid in nodes:
                fail(f"Duplicate activity ID: {uid}.")
            if len(nodes) >= 2000:
                fail("A syllabus may have at most 2000 activities.")
            nodes[uid] = node
            ancestors[uid] = parents
            kind = node.get("kind", "module" if node.get("children") else "read")
            if not isinstance(kind, str) or kind not in KINDS:
                fail(f"Unknown activity kind: {kind}.")
            for key in ("notes", "mermaid", "ascii"):
                text(node, key)
            art_piece(node)
            if "optional" in node and type(node["optional"]) is not bool:
                fail(f"optional must be true or false: {uid}.")
            resource = node.get("resource")
            if resource is not None and (not isinstance(resource, str) or resource not in resources):
                fail(f"Unknown resource {resource!r} in {uid}.")
            if node.get("chapter") is not None and (not isinstance(node["chapter"], str) or not resource or node["chapter"] not in resources[resource]):
                fail(f"Unknown chapter in {uid}.")
            requires = node.get("requires", [])
            if not isinstance(requires, list) or any(not isinstance(r, str) for r in requires):
                fail(f"requires must be a list of activity IDs: {uid}.")
            walk(objects(node, "children"), (*parents, uid))
    walk(objects(data, "modules"))
    for uid, node in nodes.items():
        for required in node.get("requires", []):
            if required not in nodes:
                fail(f"Unknown prerequisite {required!r} in {uid}.")
            if required == uid or required in ancestors[uid] or uid in ancestors[required]:
                fail(f"Prerequisite creates a parent/child dependency cycle: {uid}.")
    # Include containment edges: completing a module depends on its descendants.
    graph = {uid: list(node.get("requires", [])) + [c["id"] for c in node.get("children", [])]
             for uid, node in nodes.items()}
    visited, visiting = set(), set()
    def visit(uid):
        if uid in visiting:
            fail(f"Prerequisite cycle involving {uid}.")
        if uid in visited:
            return
        visiting.add(uid)
        for dependency in graph[uid]:
            visit(dependency)
        visiting.remove(uid)
        visited.add(uid)
    try:
        for uid in nodes:
            visit(uid)
    except RecursionError as exc:
        raise SyllabusError("Prerequisite chain is too deep.") from exc
    skills = set()
    for skill in objects(data, "skills"):
        uid = identity(skill)
        if uid in skills:
            fail(f"Duplicate skill ID: {uid}.")
        skills.add(uid)
        criteria = skill.get("criteria", [])
        if not isinstance(criteria, list) or any(not isinstance(c, str) or c not in nodes for c in criteria):
            fail(f"Skill {uid} has unknown activity criteria.")
    return data


def flatten_syllabus(data):
    rows = []
    def walk(children, parent=None):
        for position, node in enumerate(children):
            rows.append({**node, "parent": parent, "position": position})
            walk(node.get("children", []), node["id"])
    walk(data.get("modules", []))
    return rows
