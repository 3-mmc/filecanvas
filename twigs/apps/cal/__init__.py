"""cal: your calendars (.ics feeds and files), reminders, and what-ifs that fork a day."""
APP = dict(name='cal', title='calendar', alias='calcanvas', summary='calendars, reminders, what-ifs',
           cli='twigs.apps.cal.cli:main', hub='twigs.apps.cal.cli:providers', needs=[])
