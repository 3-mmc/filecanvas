"""Feeds: fetched into calendars/<key>/ (asking first whether they've changed), what goes wrong
said without the secret address, and feeds.toml kept for you alone.

    python3 -m unittest tests.test_feeds"""
import datetime as dt, http.server, os, stat, threading, unittest

import tests                                    # the private test home, before calcanvas

from calcanvas import cal, feeds

ICS = ('BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART;VALUE=DATE:20261117\r\nSUMMARY:Marks out\r\n'
       'END:VEVENT\r\nEND:VCALENDAR\r\n')


class Server(http.server.BaseHTTPRequestHandler):
    body, etag, asked = ICS, '"v1"', []

    def do_GET(self):
        Server.asked.append((self.path, self.headers.get('If-None-Match')))
        if self.path.startswith('/gone'):
            self.send_response(404); self.end_headers(); return
        if self.headers.get('If-None-Match') == Server.etag:
            self.send_response(304); self.end_headers(); return
        data = Server.body.encode()
        self.send_response(200)
        if Server.etag:
            self.send_header('ETag', Server.etag)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class Feeds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.HTTPServer(('127.0.0.1', 0), Server)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        Server.body, Server.etag, Server.asked = ICS, '"v1"', []

    def test_fetched_once_then_only_asked_whether_it_changed(self):
        f = feeds.Feed('LSE dates', self.base + '/private-s3cret/basic.ics')
        self.assertEqual(feeds.fetch(f, force=True), 'new')
        with open(os.path.join(cal.CALENDARS, 'lse-dates', 'displayname')) as fh:
            self.assertEqual(fh.read().strip(), 'LSE dates')
        self.assertEqual(feeds.fetch(f), 'same')
        self.assertEqual(Server.asked[-1][1], '"v1"')              # it asked; nothing came
        Server.body, Server.etag = ICS.replace('UID:x', 'UID:x\r\nDTSTAMP:20261030T120000Z'), '"v1b"'
        self.assertEqual(feeds.fetch(f), 'new')
        Server.body, Server.etag = ICS.replace('UID:x', 'UID:x\r\nDTSTAMP:20261030T121500Z'), None
        self.assertEqual(feeds.fetch(f), 'same')                   # only stamped again, as Google does
        Server.body, Server.etag = ICS.replace('Marks out', 'Marks released'), '"v2"'
        self.assertEqual(feeds.fetch(f), 'new')
        p = cal.Calendar(plans=None, watch=0, today=dt.date(2026, 11, 1))
        self.assertEqual([e.title for e in p.by_day[dt.date(2026, 11, 17)]], ['Marks released'])
        self.assertEqual(p.names, ['LSE dates'])

    def test_webcal_addresses_and_errors_that_keep_the_secret(self):
        self.assertEqual(feeds.address('webcal://example.com/x.ics'), 'https://example.com/x.ics')
        f = feeds.Feed('gone', self.base + '/gone/private-s3cret/basic.ics')
        with self.assertRaises(OSError) as e:
            feeds.fetch(f, force=True)
        self.assertIn('404', str(e.exception))
        self.assertNotIn('s3cret', str(e.exception))
        Server.body = '<html>sign in</html>'
        with self.assertRaises(OSError) as e:
            feeds.fetch(feeds.Feed('page', self.base + '/s3cret'), force=True)
        self.assertIn("didn't send a calendar", str(e.exception))

    def test_feeds_toml_round_trips_and_only_you_can_read_it(self):
        got = [feeds.Feed('Mine "quoted"', 'https://calendar.google.com/calendar/ical/a%40b/private-x/basic.ics'),
               feeds.Feed('Holidays', 'webcal://example.com/h.ics', every=60)]
        feeds.save(got)
        self.assertEqual(stat.S_IMODE(os.stat(feeds.FEEDS).st_mode), 0o600)
        back = feeds.load()
        self.assertEqual([(f.name, f.url, f.key, f.every) for f in back],
                         [(f.name, f.url, f.key, f.every) for f in got])

    def test_the_canvas_fetches_in_the_background_and_says_so(self):
        f = feeds.Feed('bg', self.base + '/bg.ics')
        done = threading.Event()

        def fetch(feed):
            try:
                return feeds.fetch(feed)
            finally:
                done.set()
        p = cal.Calendar(plans=None, watch=0, feeds=[f], fetch=fetch)
        self.assertTrue(done.wait(5))
        import time
        for _ in range(50):
            got = [s for s in p.signals() if s[0] == 'status']
            if any(s[3] == 'ok' for s in got):
                break
            time.sleep(0.05)
        self.assertTrue(os.path.exists(os.path.join(cal.CALENDARS, 'bg', 'feed.ics')))


if __name__ == '__main__':
    unittest.main()
