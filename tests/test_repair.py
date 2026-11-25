import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import capture_merge
import parts
import repairs
from repair_cafe import load, snapshot
from store import connect, one

ROOT = Path(__file__).resolve().parents[1]
EVENTS = json.loads((ROOT / 'fixtures/demo.json').read_text(encoding='utf-8'))
STATION_A = json.loads((ROOT / 'fixtures/station_a.json').read_text(encoding='utf-8'))
STATION_B = json.loads((ROOT / 'fixtures/station_b.json').read_text(encoding='utf-8'))
BASE = EVENTS[:14]  # flex has reserved tab, nothing fitted yet


def intake_batch():
    return capture_merge.normalize([('station_a.json', STATION_A), ('station_b.json', STATION_B)])


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.db = connect(':memory:')
        self.addCleanup(self.db.close)

    def test_matching_uses_permission_and_slot(self):
        load(self.db, EVENTS[:12])
        self.assertEqual(repairs.plan(self.db), {'flex': 'hinge'})
        load(self.db, EVENTS[12:13])
        self.assertTrue(parts.usable(self.db, 'tab'))
        self.assertIsNone(parts.extra_time(self.db, 'H', 'T'))
        self.assertEqual(repairs.plan(self.db), {'flex': 'tab', 'strict': 'hinge'})
        load(self.db, [['c', 'cancel', 'flex'], ['b', 'book', 'flex', 'left', 'mei', 0, 19]])
        self.assertEqual(repairs.plan(self.db), {'flex': 'hinge'})

    def test_booking_conflict_and_skill(self):
        load(self.db, EVENTS[:11])
        load(self.db, [['v', 'volunteer', 'ren', 'other']])
        for station, worker, reason in [('right', 'mei', 'overlap'), ('left', 'lin', 'overlap'), ('right', 'ren', 'skill')]:
            with self.assertRaisesRegex(ValueError, reason):
                load(self.db, [['b', 'book', 'strict', station, worker, 19, 35]])
        load(self.db, EVENTS[11:12])
        self.assertEqual(one(self.db, "SELECT start FROM bookings WHERE job='strict'"), (20,))

    def test_recheck_and_retention(self):
        load(self.db, EVENTS[:15])
        for op, args, reason in [('check', ['strict','mei',1], 'independent'), ('cancel', ['strict'], 'allocation'),
                                 ('retention', ['tab','keep'], 'committed'), ('handover', ['strict'], 'blocked')]:
            with self.assertRaisesRegex(ValueError, reason):
                load(self.db, [['bad', op, *args]])
        load(self.db, EVENTS[15:16])
        with self.assertRaisesRegex(ValueError, 'blocked'):
            load(self.db, [['h', 'handover', 'strict']])
        load(self.db, EVENTS[16:]); load(self.db, EVENTS)
        self.assertEqual(one(self.db, "SELECT fitting,returned FROM allocations WHERE job='strict'"), ('f2', 1))
        with self.assertRaisesRegex(ValueError, 'open allocation'):
            load(self.db, [['bad', 'release', 'strict']])

    def test_stale_positive_and_atomic_retry(self):
        load(self.db, EVENTS)
        before = snapshot(self.db)
        adverse = ['late','inspect','tab',4,0]
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            load(self.db, [adverse, ['f','fit','flex']])
        self.assertEqual(snapshot(self.db), before)
        load(self.db, [adverse, ['stale','inspect','tab',3,1]])
        self.assertFalse(parts.usable(self.db, 'tab'))
        with self.assertRaisesRegex(ValueError, 'id conflict'):
            load(self.db, [['late','inspect','tab',4,1]])
        with self.assertRaises(sqlite3.IntegrityError):
            load(self.db, [['dup','inspect','tab',4,1]])
        load(self.db, [['new','inspect','tab',5,1], ['f','fit','flex']])
        self.assertEqual(one(self.db, "SELECT fitting FROM allocations WHERE job='flex'"), ('f',))

    def test_inspection_revision_requires_current_pass_for_same_fitting(self):
        load(self.db, EVENTS[:20])
        load(self.db, [['higher', 'inspect', 'hinge', 3, 1]])
        with self.assertRaisesRegex(ValueError, 'handover blocked'):
            load(self.db, EVENTS[20:])
        load(self.db, [['older', 'inspect', 'hinge', 2, 0], ['q3', 'check', 'strict', 'lin', 1]])
        load(self.db, EVENTS[20:])
        self.assertEqual(one(self.db, "SELECT inspection_rev FROM checks WHERE id='q3'"), (3,))
        self.assertEqual(one(self.db, "SELECT inspection_rev,status FROM fittings WHERE id='f2'"), (1, 'returned'))

    def test_released_fitting_keeps_original_worker_and_part_evidence(self):
        load(self.db, EVENTS[:16])
        with self.assertRaisesRegex(ValueError, 'overlap'):
            load(self.db, [EVENTS[16], ['late-job', 'request', 'replacement', 'H', 'handwork', 10, 4],
                           ['busy', 'book', 'replacement', 'left', 'lin', 10, 15]])
        self.assertEqual(one(self.db, "SELECT status FROM fittings WHERE id='f1'"), ('mounted',))
        load(self.db, [EVENTS[16], ['cancel-strict', 'cancel', 'strict'],
                       ['rebook', 'book', 'strict', 'right', 'lin', 20, 40],
                       *EVENTS[17:19]])
        self.assertEqual(one(self.db, "SELECT worker,part,inspection_rev,status FROM fittings WHERE id='f1'"),
                         ('mei', 'hinge', 1, 'removed'))
        self.assertEqual(one(self.db, "SELECT worker,status FROM fittings WHERE id='f2'"), ('lin', 'mounted'))
        with self.assertRaisesRegex(ValueError, 'independent'):
            load(self.db, EVENTS[19:20])
        load(self.db, [['other-review', 'check', 'strict', 'mei', 1], EVENTS[20]])

    def test_old_pass_does_not_clear_refit(self):
        load(self.db, EVENTS[:15])
        load(self.db, [['good','check','strict','lin',1], *EVENTS[16:19]])
        with self.assertRaisesRegex(ValueError, 'blocked'):
            load(self.db, EVENTS[20:])
        load(self.db, EVENTS[19:])
        self.assertEqual(one(self.db, "SELECT returned FROM allocations WHERE job='strict'"), (1,))
