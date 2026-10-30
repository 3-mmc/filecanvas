"""Thunderbird's existing mail accounts on the canvas."""
APP = dict(name='thunderbird', title='Thunderbird', alias=None,
           summary='mail through a running Thunderbird', cli='twigs.apps.thunderbird.cli:main',
           hub='twigs.apps.thunderbird.cli:sections', needs=[])
