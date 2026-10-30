"""The calendar: .ics read (time zones, all-day, repeats), months laid out, clashes and what-ifs
forking a day, and keeping one.

    python3 -m unittest tests.test_cal"""
import datetime as dt, json, os, re, shutil, tempfile, unittest
from zoneinfo import ZoneInfo

import tests                                    # the private test home, before msgcanvas

from msgcanvas import cal, treecanvas as fc

NY = ZoneInfo('America/New_York')
TODAY = dt.date(2026, 10, 30)
ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]')


def ics(*events):
    return 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\n' + ''.join(
        'BEGIN:VEVENT\r\n' + ''.join(l + '\r\n' for l in e) + 'END:VEVENT\r\n' for e in events) + 'END:VCALENDAR\r\n'


class Reading(unittest.TestCase):
    def parse(self, *events):
        return cal.parse(ics(*events), 'test', NY, dt.date(2030, 1, 1))

    def test_times_come_here_and_all_day_stays_a_date(self):
        es = self.parse(['UID:a', 'DTSTART:20261231T120000Z', 'DTEND:20261231T130000Z', 'SUMMARY:noon UTC'],
                        ['UID:b', 'DTSTART;TZID=Europe/London:20261214T170000', 'DURATION:PT90M',
                         'SUMMARY:Conference\\, at last', 'LOCATION:Granada\r\n  Theatre'],
                        ['UID:c', 'DTSTART;VALUE=DATE:20261024', 'DTEND;VALUE=DATE:20261101', 'SUMMARY:Visit'],
                        ['UID:d', 'DTSTART;VALUE=DATE:20261117', 'SUMMARY:Marks'],
                        ['UID:e', 'DTSTART:20261101T100000Z', 'STATUS:CANCELLED', 'SUMMARY:gone'])
        by = {e.uid: e for e in es}
        self.assertEqual(set(by), {'a', 'b', 'c', 'd'})
        self.assertEqual(by['a'].at(), '07:00')
        self.assertEqual((by['b'].title, by['b'].where, by['b'].at()), ('Conference, at last', 'Granada Theatre', '12:00'))
        self.assertEqual(cal.when_text(by['b']), 'Mon 14 Dec  12:00 → 13:30')
        self.assertTrue(by['c'].span)
        self.assertEqual((by['c'].first, by['c'].last), (dt.date(2026, 10, 24), dt.date(2026, 10, 31)))
        self.assertFalse(by['d'].span)
        self.assertEqual(by['d'].last, dt.date(2026, 11, 17))

    def test_a_night_out_is_the_night_it_starts(self):
        e, = self.parse(['UID:n', 'DTSTART:20261101T010000Z', 'DTEND:20261101T090000Z', 'SUMMARY:party'])
        self.assertEqual((e.first, e.span), (dt.date(2026, 10, 31), False))

    def test_repeats_skip_exdates_and_moved_ones(self):
        es = self.parse(['UID:r', 'DTSTART;VALUE=DATE:20240229', 'RRULE:FREQ=YEARLY;COUNT=3', 'SUMMARY:leap'],
                        ['UID:w', 'DTSTART;TZID=America/New_York:20261102T090000', 'DTEND;TZID=America/New_York:20261102T100000',
                         'RRULE:FREQ=WEEKLY;UNTIL=20261124T000000Z', 'EXDATE;TZID=America/New_York:20261109T090000',
                         'SUMMARY:seminar'],
                        ['UID:w', 'RECURRENCE-ID;TZID=America/New_York:20261116T090000',
                         'DTSTART;TZID=America/New_York:20261117T150000', 'SUMMARY:seminar (moved)'])
        days = sorted((e.first.isoformat(), e.title) for e in es if e.title != 'leap') + \
            sorted((e.first.isoformat(), e.title) for e in es if e.title == 'leap')
        self.assertEqual(days, [('2026-11-02', 'seminar'), ('2026-11-17', 'seminar (moved)'),
                                ('2026-11-23', 'seminar'), ('2024-02-29', 'leap'), ('2028-02-29', 'leap')])


class Canvas(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='msgcanvas-cal-')
        self.addCleanup(shutil.rmtree, self.home, True)
        folder = os.path.join(self.home, 'calendars', 'mine')
        os.makedirs(folder)
        with open(os.path.join(folder, 'displayname'), 'w') as f:
            f.write('Mine')
        with open(os.path.join(folder, 'all.ics'), 'w') as f:
            f.write(ics(['UID:drng', 'DTSTART;TZID=America/New_York:20261015T110000', 'DURATION:PT1H', 'SUMMARY:Appt with Dr Ng'],
                        ['UID:visit', 'DTSTART;VALUE=DATE:20261024', 'DTEND;VALUE=DATE:20261101', 'SUMMARY:Tomasz in London'],
                        ['UID:maria', 'DTSTART;TZID=America/New_York:20261031T210000',
                         'DTEND;TZID=America/New_York:20261101T040000', "SUMMARY:Maria's Halloween"],
                        ['UID:petra', 'DTSTART;TZID=America/New_York:20261031T220000',
                         'DTEND;TZID=America/New_York:20261101T000000', "SUMMARY:Petra's birthday"],
                        ['UID:fly', 'DTSTART;TZID=America/New_York:20261123T115000', 'DURATION:PT4H', 'SUMMARY:Flight']))
        self.plans = os.path.join(self.home, 'whatif.json')
        self.p = cal.Calendar(os.path.join(self.home, 'calendars'), self.plans, today=TODAY, watch=0)
        self.c = fc.Canvas('', fc.options(provider=self.p, backdrop='off', ease=1, glyphs='plain', colmax=40))

    def screen(self):
        return '\n'.join(ANSI.sub('', l).rstrip() for l in self.c.frame(150, 24, animate=False)[0])

    def press(self, *keys):
        for k in keys:
            fc.apply(self.c, k, self.c.opts)

    def type(self, text):
        self.press(*text, '\r')

    def test_opens_on_this_month_on_today_and_spans_run_beside_days(self):
        self.assertEqual(self.c.focus.path, f'{cal.ROOT}/2026-10/2026-10-30')
        month = self.c.focus.parent
        self.assertEqual([k.name for k in month.shown()],
                         ['Thu 15  Appt with Dr Ng', 'Sat 24–Sat 31  Tomasz in London', 'Fri 30  today',
                          "Sat 31  Maria's Halloween or Petra's birthday"])
        self.assertEqual(self.p.count(month.shown()[0]), ('11:00', 'stub', False))
        self.assertEqual(self.p.count(month.shown()[1]), ('8 days', 'stub', False))
        self.assertEqual(self.p.count(month), ('3', 'stub', False))   # today, empty, isn't one
        s = self.screen()
        self.assertIn('1 to decide', s)
        self.assertIn('October Y', s)

    def test_a_clash_forks_the_day_and_keeping_one_lets_the_rest_go(self):
        self.press('down', 'right')
        maria, petra = self.c.focus.parent.shown()
        self.assertEqual((maria.kind, self.p.mark(maria)), ('alt', ('git_dirty', '○', 'o')))
        self.press('\x0b')
        self.assertEqual(self.p.mark(maria), ('git_staged', '✓', '+'))
        self.assertEqual(self.p.line(maria), 'git_staged')
        self.assertEqual(self.p.dim(petra), 0.45)
        with open(self.plans) as f:
            self.assertEqual(json.load(f)['days']['2026-10-31']['kept'], 'ev:maria')
        self.assertIn("kept Maria's Halloween", self.screen())
        self.assertNotIn('to decide', self.screen())
        self.assertEqual(self.c.focus.parent.name, "Sat 31  Maria's Halloween")   # the day says what you'll do
        self.press('\x0b')
        self.assertIsNone(self.p.kept(dt.date(2026, 10, 31), maria.data['alts']))

    def test_what_if_on_an_empty_day_weighs_it_against_keeping_it_free(self):
        self.press('\x17')
        self.type('call Dad')
        self.screen()
        today = self.c.focus
        self.assertEqual(today.name, 'Fri 30  keep the day free or call Dad')
        self.press('right')
        free, plan = today.shown()
        self.assertEqual((free.name, plan.name, self.p.mark(plan)), ('keep the day free', 'call Dad',
                                                                     ('git_untracked', '?', '?')))
        self.press('down', '\x0e')
        self.type('book the flight')
        self.screen()
        self.assertEqual([k.name for k in self.c.focus.parent.shown()[1].shown()], ['then  book the flight'])
        again = cal.Calendar(os.path.join(self.home, 'calendars'), self.plans, today=TODAY, watch=0)
        self.assertEqual(again.plans.get(dt.date(2026, 10, 30))['plans'][0]['steps'], ['book the flight'])

    def test_what_if_instead_of_an_event_forks_it(self):
        self.press('up', 'up', '\x17')
        self.type('lunch with Mum')
        self.screen()
        day = self.c.focus
        self.assertEqual(self.p.fork_of(day)[0][2].title, 'Appt with Dr Ng')
        self.assertEqual(day.name, 'Thu 15  Appt with Dr Ng or lunch with Mum')

    def test_what_if_from_a_month_takes_the_day_first(self):
        self.press('left', 'down', '\x17')
        self.type('23 drive instead')
        self.screen()
        nov = self.c.focus
        day = next(k for k in nov.shown() if k.data.get('day') == dt.date(2026, 11, 23))
        self.assertEqual(day.name, 'Mon 23  Flight  +1')        # the flight, then the fork
        self.assertEqual([self.p.label(k) for k in self.p.children(day)], ['Flight', 'as it is or drive instead'])


if __name__ == '__main__':
    unittest.main()
