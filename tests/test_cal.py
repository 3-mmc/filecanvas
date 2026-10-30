"""The calendar: .ics read (time zones, all-day, repeats), months laid out, clashes and what-ifs
forking a day, and keeping one.

    python3 -m unittest tests.test_cal"""
import datetime as dt, json, os, re, shutil, tempfile, unittest
from zoneinfo import ZoneInfo

import tests                                    # the private test home, before calcanvas

from calcanvas import cal, treecanvas as fc

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
        self.assertEqual(cal.when_text(by['b']), '14 Mon Dec  12:00 → 13:30')
        self.assertEqual(cal.when_text(by['c']), '24 Sat Oct → 31 Sat Oct, 8 days')
        self.assertEqual(cal.when_text(by['d']), '17 Tue Nov 2026, all day')
        self.assertTrue(by['c'].span)
        self.assertEqual((by['c'].first, by['c'].last), (dt.date(2026, 10, 24), dt.date(2026, 10, 31)))
        self.assertFalse(by['d'].span)
        self.assertEqual(by['d'].last, dt.date(2026, 11, 17))

    def test_a_night_out_is_the_night_it_starts(self):
        e, = self.parse(['UID:n', 'DTSTART:20261101T010000Z', 'DTEND:20261101T090000Z', 'SUMMARY:party'])
        self.assertEqual((e.first, e.span), (dt.date(2026, 10, 31), False))
        e, w = self.parse(['UID:d', 'DTSTART:20261122T030000Z', 'DTEND:20261123T030000Z', 'SUMMARY:nonstop'],
                          ['UID:w', 'DTSTART:20260919T020000Z', 'DTEND:20260921T020000Z', 'SUMMARY:weekender'])
        self.assertEqual((e.first, e.span, w.span), (dt.date(2026, 11, 21), False, True))   # 24h a night; 48h an era

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

    def test_weekdays_nth_weekdays_and_month_days(self):
        es = self.parse(['UID:mw', 'DTSTART;TZID=America/New_York:20261102T100000', 'DURATION:PT1H',
                         'RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4', 'SUMMARY:class'],
                        ['UID:tue', 'DTSTART;VALUE=DATE:20261110', 'RRULE:FREQ=MONTHLY;BYDAY=2TU;COUNT=3', 'SUMMARY:2nd Tue'],
                        ['UID:fri', 'DTSTART;VALUE=DATE:20261030', 'RRULE:FREQ=MONTHLY;BYDAY=-1FR;UNTIL=20270101', 'SUMMARY:last Fri'],
                        ['UID:eom', 'DTSTART;VALUE=DATE:20261031', 'RRULE:FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=3', 'SUMMARY:month end'],
                        ['UID:bi', 'DTSTART;VALUE=DATE:20261101', 'RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=SU;COUNT=3', 'SUMMARY:fortnightly'],
                        ['UID:th', 'DTSTART;VALUE=DATE:20261126', 'RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=4TH;COUNT=2',
                         'SUMMARY:Thanksgiving'])
        got = {}
        for e in es:
            got.setdefault(e.title, []).append(e.first.isoformat())
        self.assertEqual(got['class'], ['2026-11-02', '2026-11-04', '2026-11-09', '2026-11-11'])
        self.assertEqual(got['2nd Tue'], ['2026-11-10', '2026-12-08', '2027-01-12'])
        self.assertEqual(got['last Fri'], ['2026-10-30', '2026-11-27', '2026-12-25'])
        self.assertEqual(got['month end'], ['2026-10-31', '2026-11-30', '2026-12-31'])
        self.assertEqual(got['fortnightly'], ['2026-11-01', '2026-11-15', '2026-11-29'])
        self.assertEqual(got['Thanksgiving'], ['2026-11-26', '2027-11-25'])

    def test_repeats_keep_their_own_zones_clock(self):
        # 9:00 in London, weekly: the UK's clocks change on 25 Oct, New York's on 1 Nov, so in between
        # it's 4:00 here, either side 5:00 then 4:00 again
        es = self.parse(['UID:ldn', 'DTSTART;TZID=Europe/London:20261019T090000', 'DURATION:PT1H',
                         'RRULE:FREQ=WEEKLY;COUNT=3', 'SUMMARY:standup'])
        self.assertEqual([e.at() for e in es], ['04:00', '05:00', '04:00'])


class Canvas(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='calcanvas-')
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
                         ['15 Thu  Appt with Dr Ng', '24 Sat → 31 Sat  Tomasz in London', '30 Fri  today',
                          "31 Sat  21:00 Maria's Halloween or Petra's birthday"])
        self.assertEqual(self.p.count(month.shown()[0]), ('11:00', 'stub', False))
        self.assertEqual(self.p.count(month.shown()[1]), ('day 7 of 8', cal.SOON_COLOUR, True))   # you're in it
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
        self.assertEqual(self.c.focus.parent.name, "31 Sat  21:00 Maria's Halloween")   # the day says what you'll do
        self.press('\x0b')
        self.assertIsNone(self.p.kept(dt.date(2026, 10, 31), maria.data['alts']))

    def test_what_if_on_an_empty_day_weighs_it_against_keeping_it_free(self):
        self.press('\x17')
        self.type('call Dad')
        self.screen()
        today = self.c.focus
        self.assertEqual(today.name, '30 Fri  keep the day free or call Dad')
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
        self.assertEqual(day.name, '15 Thu  Appt with Dr Ng or lunch with Mum')

    def test_what_if_from_a_month_takes_the_day_first(self):
        self.press('left', 'down', '\x17')
        self.type('23 drive instead')
        self.screen()
        nov = self.c.focus
        day = next(k for k in nov.shown() if k.data.get('day') == dt.date(2026, 11, 23))
        self.assertEqual(day.name, '23 Mon  Flight  +1')        # the flight, then the fork
        self.assertEqual([self.p.label(k) for k in self.p.children(day)], ['Flight', 'as it is or drive instead'])

    def test_close_things_put_their_time_first_and_say_how_long(self):
        self.press('down', 'right')
        maria, petra = self.c.focus.parent.shown()
        self.assertEqual((maria.name, petra.name), ("21:00 Maria's Halloween", "22:00 Petra's birthday"))
        self.assertEqual(self.p.count(maria), ('tomorrow', cal.SOON_COLOUR, True))
        self.assertEqual(self.p.tint(maria), [(5, cal.SOON_COLOUR, True)])
        day = self.c.focus.parent
        self.assertEqual(self.p.tint(day)[-1], (5, cal.SOON_COLOUR, True))      # after the day's number and name
        self.assertEqual(self.p.count(day), ('tomorrow', cal.SOON_COLOUR, True))
        drng = self.c.focus.parent.parent.shown()[0]
        self.assertEqual((drng.name, self.p.count(drng)), ('15 Thu  Appt with Dr Ng', ('11:00', 'stub', False)))
        later = cal.Calendar(os.path.join(self.home, 'calendars'), self.plans, watch=0,
                             now=dt.datetime(2026, 10, 31, 20, 30, tzinfo=NY))
        self.assertEqual(later.soon(later.by_day[dt.date(2026, 10, 31)][0]), 'in 30m')

    def test_a_span_is_an_era_holding_what_happens_in_it(self):
        self.press('up')                # onto Tomasz in London
        era = self.c.focus
        self.assertEqual(era.kind, 'span')
        self.press('right')
        self.assertEqual([k.name for k in era.shown()],
                         ['24 Sat Oct → 31 Sat Oct, 8 days', '30 Fri  today',
                          "31 Sat  21:00 Maria's Halloween or Petra's birthday"])
        self.assertEqual(self.c.focus.name, '30 Fri  today')             # on what's next in it
        self.press('down', 'right', '\x0b')                            # a fork kept in the era…
        self.screen()
        month = era.parent
        self.assertEqual(month.shown()[-1].name, "31 Sat  21:00 Maria's Halloween")   # …is kept in the month

    def test_each_week_a_shade_of_its_months_colour(self):
        oct_ = [cal.shade(dt.date(2026, 10, d)) for d in (1, 5, 12, 19, 26)]
        self.assertEqual(len(set(oct_)), 5)                              # every week its own
        self.assertEqual(cal.shade(dt.date(2026, 10, 5)), cal.shade(dt.date(2026, 10, 11)))   # Monday to Sunday
        self.assertEqual(cal.shade(dt.date(2026, 10, 1)), cal.MONTHS[10])
        h = lambda c: __import__('colorsys').rgb_to_hls(*(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)))[0]
        self.assertTrue(all(abs(h(c) - h(cal.MONTHS[10])) < 0.02 for c in oct_))     # all orange


if __name__ == '__main__':
    unittest.main()
