"""Opening sections on one canvas: [(name, provider)], one alone, several as named branches from
the root (Together), with the opening screen while they load, and each closed after (a chat's nchat)."""
import os

from twigs import core as fc


def open(sections, start='', intro=True, **look):
    from . import together
    providers = [p for _, p in sections]
    for p in providers:          # chats ask for their list at once, so it's there by the time it's shown
        serve = getattr(p, 'serve', None)
        if serve is not None:
            serve.ready.wait(3)
            for prof in serve.profiles.values():
                prof.asked_chats = True
                serve.request('get_chats', profile=prof.id)
    if intro and all(hasattr(p, 'loading') for p in providers):
        from twigs.apps.msg import intro as splash
        loading = lambda: [p.loading() for p in providers]
        splash.intro(lambda: '  ·  '.join(text for text, _ in loading()), lambda: all(done for _, done in loading()))
    try:
        return fc.pick(start, provider=providers[0] if len(providers) == 1 else together.Together(sections), **look)
    finally:
        for p in providers:
            if getattr(p, 'serve', None) is not None:
                p.serve.close()
