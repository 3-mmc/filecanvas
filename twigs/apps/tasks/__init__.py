"""tasks: tuido's desk (paths, library, habits, projects, today) on the canvas, with due days and timers."""
APP = dict(name='tasks', title='tasks', alias=None, summary="tuido's desk: due days, timers",
           cli='twigs.apps.tasks.cli:main', hub='twigs.apps.tasks.cli:sections',
           needs=[('command:tuido', 'tuido: github.com/3-mmc/TuiDo (uv tool install)')])
