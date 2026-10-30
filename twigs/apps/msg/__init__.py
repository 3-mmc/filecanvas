"""msg: independent optional mail and chat capabilities."""
FEATURE_NEEDS = {
    'mail': [('mbsync', 'isync'), ('notmuch', 'notmuch'), ('msmtp', 'msmtp'), ('pass', 'pass'),
             ('module:notmuch2', 'the notmuch CFFI bindings; run twigs doctor mail')],
    'chat': [('nchat', 'the twigs nchat fork: github.com/3-mmc/nchat')],
}
APP = dict(name='msg', title='mail & chats', alias='msgcanvas', summary='mail (notmuch) and chats (nchat)',
           cli='twigs.apps.msg.cli:main', hub='twigs.apps.msg.cli:sections',
           needs=[], feature_needs=FEATURE_NEEDS)
