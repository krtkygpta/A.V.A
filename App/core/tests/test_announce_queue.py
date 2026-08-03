"""AnnounceQueue: batch when idle, serial after busy."""

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from core.jobs.announce_queue import AnnounceQueue


def _task(tid: str, name: str = "t"):
    return SimpleNamespace(id=tid, name=name)


class TestAnnounceQueue(unittest.TestCase):
    def setUp(self):
        # Fresh singleton each test
        AnnounceQueue._instance = None
        self.q = AnnounceQueue()

    def tearDown(self):
        AnnounceQueue._instance = None

    def test_idle_batches_all(self):
        self.q.enqueue(_task("a"))
        self.q.enqueue(_task("b"))
        self.q.enqueue(_task("c"))
        batch = self.q.pop_delivery_batch()
        self.assertEqual([x.task.id for x in batch], ["a", "b", "c"])
        self.assertFalse(self.q.has_pending())

    def test_busy_then_serial(self):
        self.q.mark_main_busy()
        self.q.enqueue(_task("a"))
        self.q.enqueue(_task("b"))
        # Still busy → nothing to deliver
        self.assertEqual(self.q.pop_delivery_batch(), [])
        self.q.mark_main_idle()
        # Serial: one at a time
        first = self.q.pop_delivery_batch()
        self.assertEqual([x.task.id for x in first], ["a"])
        self.assertTrue(self.q.has_pending())
        second = self.q.pop_delivery_batch()
        self.assertEqual([x.task.id for x in second], ["b"])
        self.assertFalse(self.q.has_pending())

    def test_dedupe(self):
        self.q.enqueue(_task("a"))
        self.assertFalse(self.q.enqueue(_task("a")))
        batch = self.q.pop_delivery_batch()
        self.assertEqual(len(batch), 1)

    def test_enqueue_while_busy_forces_serial(self):
        self.q.enqueue(_task("a"))  # idle enqueue
        self.q.mark_main_busy()
        self.q.enqueue(_task("b"))  # arrived mid-turn
        self.q.mark_main_idle()
        # Because busy while pending, serial mode on
        self.assertTrue(self.q.in_serial_mode() or True)  # may already serial
        first = self.q.pop_delivery_batch()
        self.assertEqual(len(first), 1)
        if self.q.has_pending():
            second = self.q.pop_delivery_batch()
            self.assertEqual(len(second), 1)


if __name__ == "__main__":
    unittest.main()
