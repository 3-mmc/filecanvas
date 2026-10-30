"""msg: mail (notmuch, mbsync, msmtp) and chats (nchat) on one canvas."""
APP = dict(name='msg', title='mail & chats', alias='msgcanvas', summary='mail (notmuch) and chats (nchat)',
           cli='twigs.apps.msg.cli:main', hub='twigs.apps.msg.cli:sections',
           needs=[('mbsync', 'isync'), ('notmuch', 'notmuch'), ('msmtp', 'msmtp'), ('pass', 'pass'),
                  ('module:notmuch2', 'python3-notmuch2'), ('nchat', 'nchat, for chats: github.com/3-mmc/nchat')])
