import json
import unittest
from contextlib import closing
from pathlib import Path

from repair_cafe import load, snapshot
from store import connect


DEMO = Path(__file__).resolve().parents[1] / 'fixtures' / 'demo.json'
CARD = ['card', 'probe-card', 'f1', 'q1', 'side-press', {'0': 'loose', '1': 'binding'}]
ISSUE = ['paper', 'probe-issue', 'card']
READING = ['reading', 'probe-read', 'card', 'paper', '1']


class FixtureProbeTests(unittest.TestCase):
    def setUp(self):
        demo = json.loads(DEMO.read_text(encoding='utf-8'))
        self.prefix = demo[:next(i for i, row in enumerate(demo) if row[0] == 'undo')]
        self.db = connect(':memory:')
        self.addCleanup(self.db.close)
        load(self.db, self.prefix)

    def test_a_real_probe_is_issued_and_replayed_as_a_fault(self):
        load(self.db, [CARD, ISSUE, READING, READING])
        self.assertEqual(snapshot(self.db)['probe_issues'], [('paper', 'card', 'side-press')])
        self.assertEqual(snapshot(self.db)['probe_results'], [('reading', 'paper', '1', 'binding')])
        with closing(connect(':memory:')) as replay:
            events = [json.loads(payload) for _, payload in snapshot(self.db)['journal']]
            load(replay, events)
            self.assertEqual(snapshot(self.db), snapshot(replay))
        with self.assertRaisesRegex(ValueError, 'handover blocked'):
            load(self.db, [['not-a-check', 'handover', 'strict']])

    def test_open_order_requires_the_original_fitting(self):
        load(self.db, [CARD, ISSUE, ['undo', 'release', 'strict']])
        with self.assertRaisesRegex(ValueError, 'probe card is closed'):
            load(self.db, [READING])
        self.assertEqual(snapshot(self.db)['probe_results'], [])
