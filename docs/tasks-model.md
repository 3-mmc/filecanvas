# Bundled task model

Twigs ships the standard-library model from Aaron van Blerkom's TuiDo in
`twigs/apps/tasks/model/`. It does not install a `tuido` command, import an unrelated
PyPI package named `tuido`, or depend on Textual. The standalone TuiDo interface is unchanged.

The initial snapshot is from `3-mmc/TuiDo`, commit
`914b47f5abdee9bd5db0f1b9b4d5b0b221f3c0d5` (`tuido-time`, including due dates):
`store.py`, `projects.py`, `learning.py`, `syllabus.py`, and `demo.py`.
Relative imports are preserved under the twigs namespace. The skill-level words are the same
four constants used by TuiDo, without importing its UI.

By default both interfaces use `$XDG_DATA_HOME/tuido/tuido.sqlite3`, or
`~/.local/share/tuido/tuido.sqlite3`. Existing data is retained. The model's migrations add
missing tables/columns in place; standalone TuiDo should be kept compatible when changing this
model. Undo is local to the running interface. `twigs tasks --db FILE` selects another database;
`twigs tasks --demo` uses a temporary database.

When refreshing the snapshot, compare the schema and migrations with the standalone repo,
update this provenance, and run `tests/test_tasks.py`. Do not copy the Textual interface or
change the standalone repo as part of a twigs update.
