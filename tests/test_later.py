"""Later: the times you say, and what waits for them.

    python3 -m unittest tests.test_later"""
import datetime as dt, unittest
from zoneinfo import ZoneInfo

import tests                                    # the private test home, before msgcanvas

from msgcanvas import later

NY = ZoneInfo('America/New_York')
NOW = dt.datetime(2026, 10, 30, 10, 28, tzinfo=NY)     # a Friday morning


class Saying(unittest.TestCase):
    def at(self, text):
        got = later.parse(text, NOW)
        return got and got.strftime('%a %d %b %H:%M')

    def test_how_long(self):
        self.assertEqual(self.at('2h'), 'Fri 30 Oct 12:28')
        self.assertEqual(self.at('in 30 min'), 'Fri 30 Oct 10:58')
        self.assertEqual(self.at('3d'), 'Mon 02 Nov 10:28')
        self.assertEqual(self.at('1 week'), 'Fri 06 Nov 10:28')

    def test_days_and_times(self):
        self.assertEqual(self.at('tonight'), 'Fri 30 Oct 19:00')
        self.assertEqual(self.at('tomorrow'), 'Sat 31 Oct 08:00')
        self.assertEqual(self.at('tomorrow 9'), 'Sat 31 Oct 09:00')
        self.assertEqual(self.at('Tomorrow at 2pm'), 'Sat 31 Oct 14:00')
        self.assertEqual(self.at('mon'), 'Mon 02 Nov 08:00')
        self.assertEqual(self.at('monday 9:30'), 'Mon 02 Nov 09:30')
        self.assertEqual(self.at('fri'), 'Fri 06 Nov 08:00')               # today's Friday: the next
        self.assertEqual(self.at('fri 17:00'), 'Fri 30 Oct 17:00')         # unless the time's still to come
        self.assertEqual(self.at('18:00'), 'Fri 30 Oct 18:00')
        self.assertEqual(self.at('9:00'), 'Sat 31 Oct 09:00')              # gone today: tomorrow's
        self.assertEqual(self.at('next week'), 'Mon 02 Nov 08:00')
        self.assertEqual(self.at('weekend'), 'Sat 31 Oct 08:00')

    def test_dates(self):
        self.assertEqual(self.at('3 nov'), 'Tue 03 Nov 08:00')
        self.assertEqual(self.at('nov 3 14:00'), 'Tue 03 Nov 14:00')
        self.assertEqual(self.at('3 November 2pm'), 'Tue 03 Nov 14:00')
        self.assertEqual(self.at('1 jan'), 'Fri 01 Jan 08:00')             # gone this year: next year's
        self.assertEqual(later.parse('1 jan', NOW).year, 2027)

    def test_nonsense_and_the_past_are_nothing(self):
        for text in ('', 'soon', '31 feb', '25:00', 'yesterday', '30 oct 9:00'):
            self.assertIsNone(later.parse(text, NOW), text)

    def test_said_back_number_first(self):
        self.assertEqual(later.say(later.parse('tonight', NOW), NOW), 'today 19:00')
        self.assertEqual(later.say(later.parse('tomorrow', NOW), NOW), 'tomorrow 8:00')
        self.assertEqual(later.say(later.parse('3 nov 14:00', NOW), NOW), '3 Tue Nov 14:00')


if __name__ == '__main__':
    unittest.main()
