import json
import unittest
from pathlib import Path

import counter_pickup
from repair_cafe import load, snapshot
from store import connect


class PickupTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'fixtures'
        self.demo = json.loads((root / 'demo.json').read_text(encoding='utf-8'))
        self.pickup = json.loads((root / 'counter-pickup.json').read_text(encoding='utf-8'))
        self.db = connect(':memory:')
        self.addCleanup(self.db.close)

    def test_repaired_job_can_be_tagged_and_picked_up_once(self):
        load(self.db, self.demo)
        load(self.db, self.pickup)
        load(self.db, self.pickup)
        self.assertEqual(counter_pickup.report(self.db), [
            {'tag': 'TAG-17', 'job': 'strict', 'station': 'north-desk', 'stub': 'stub-17'}
        ])
        with self.assertRaisesRegex(ValueError, 'tag already collected'):
            load(self.db, [['dup', 'counter-pickup', 'TAG-17', 'north-desk', 'stub-18']])
        replay = connect(':memory:')
        self.addCleanup(replay.close)
        load(replay, self.demo + self.pickup)
        self.assertEqual(snapshot(replay), snapshot(self.db))

    def test_cannot_tag_an_unfinished_repair(self):
        load(self.db, self.demo[:-1])
        with self.assertRaisesRegex(ValueError, 'repair is not cleared for the counter'):
            load(self.db, self.pickup[:1])
        self.assertEqual(counter_pickup.report(self.db), [])
