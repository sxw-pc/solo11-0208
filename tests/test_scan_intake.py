import copy
import json
import unittest
from pathlib import Path

from repair_cafe import load, snapshot
from scan_intake import receive
from store import connect


FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'locker-scanner.json'


class LockerIntakeTest(unittest.TestCase):
    def setUp(self):
        self.document = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.db = connect(':memory:')
        self.addCleanup(self.db.close)

    def test_receive_replay_and_receipt(self):
        events = receive(self.document)
        load(self.db, events)
        load(self.db, events)
        self.assertEqual(self.db.execute('SELECT id,policy FROM donations ORDER BY id').fetchall(),
                         [('pack-71', 'release'), ('pack-73', 'keep'), ('pack-75', 'release')])
        with self.subTest('receipt retains source frame'):
            held = self.db.execute('SELECT payload FROM journal ORDER BY rowid').fetchall()
            self.assertEqual([json.loads(row[0]) for row in held], events)
            replay = connect(':memory:')
            self.addCleanup(replay.close)
            load(replay, [json.loads(row[0]) for row in held])
            self.assertEqual(snapshot(self.db), snapshot(replay))

    def test_rejects_unsealed_or_repeated_labels_atomically(self):
        short = copy.deepcopy(self.document)
        short['bags'][1]['frames'].clear()
        with self.assertRaisesRegex(ValueError, 'incomplete bag A-10'):
            receive(short)
        duplicate = copy.deepcopy(self.document)
        duplicate['bags'][0]['frames'][1]['part'] = 'pack-71'
        with self.assertRaisesRegex(ValueError, 'repeated part pack-71'):
            receive(duplicate)
        self.assertEqual(self.db.execute('SELECT count(*) FROM donations').fetchone(), (0,))
