import time
import unittest
from types import SimpleNamespace

import numpy as np

from avatars.audio_features.base_asr import BaseASR


def make_asr(watermark_ms=300, hold_ms=1500):
    opt = SimpleNamespace(
        fps=25,
        batch_size=4,
        l=10,
        r=10,
        audio_start_watermark_ms=watermark_ms,
        underrun_hold_ms=hold_ms,
    )
    return BaseASR(opt)


def chunk(value=0.5):
    return np.full(320, value, dtype=np.float32)


class WatermarkTest(unittest.TestCase):
    def test_holds_below_watermark_without_end(self):
        asr = make_asr(watermark_ms=300)
        for _ in range(5):  # 100ms < 300ms watermark
            asr.put_audio_frame(chunk(), {"status": "start"} if not asr.queue.qsize() else {})
        frame = asr.get_audio_frame()
        self.assertEqual(frame.type, 1)
        self.assertEqual(asr.queue.qsize(), 5)  # nothing dequeued

    def test_flows_once_watermark_reached(self):
        asr = make_asr(watermark_ms=300)
        asr.put_audio_frame(chunk(), {"status": "start"})
        for _ in range(14):
            asr.put_audio_frame(chunk(), {})
        frame = asr.get_audio_frame()
        self.assertEqual(frame.type, 0)
        self.assertEqual(frame.userdata.get("status"), "start")

    def test_complete_short_response_flows_immediately(self):
        # A whole response (with its end frame) below the watermark must not wait.
        asr = make_asr(watermark_ms=300)
        asr.put_audio_frame(chunk(), {"status": "start"})
        asr.put_audio_frame(chunk(), {})
        asr.put_audio_frame(chunk(), {"status": "end"})
        types = [asr.get_audio_frame().type for _ in range(3)]
        self.assertEqual(types, [0, 0, 0])

    def test_watermark_disabled_by_default(self):
        asr = make_asr(watermark_ms=0)
        asr.put_audio_frame(chunk(), {"status": "start"})
        self.assertEqual(asr.get_audio_frame().type, 0)


class UnderrunHoldTest(unittest.TestCase):
    def test_underrun_mid_response_returns_hold_frames(self):
        asr = make_asr(watermark_ms=0, hold_ms=1500)
        asr.put_audio_frame(chunk(), {"status": "start"})
        self.assertEqual(asr.get_audio_frame().type, 0)
        frame = asr.get_audio_frame()  # queue empty, response active
        self.assertEqual(frame.type, 1)
        self.assertTrue(frame.userdata.get("hold"))

    def test_hold_expires_to_plain_silence(self):
        asr = make_asr(watermark_ms=0, hold_ms=50)
        asr.put_audio_frame(chunk(), {"status": "start"})
        asr.get_audio_frame()
        self.assertTrue(asr.get_audio_frame().userdata.get("hold"))
        time.sleep(0.08)
        frame = asr.get_audio_frame()
        self.assertEqual(frame.type, 1)
        self.assertFalse(frame.userdata.get("hold"))
        # response no longer active: further underruns are plain silence
        self.assertFalse(asr.get_audio_frame().userdata.get("hold"))

    def test_no_hold_after_end_frame(self):
        asr = make_asr(watermark_ms=0, hold_ms=1500)
        asr.put_audio_frame(chunk(), {"status": "start"})
        asr.put_audio_frame(chunk(), {"status": "end"})
        asr.get_audio_frame()
        asr.get_audio_frame()
        frame = asr.get_audio_frame()  # after end: idle, not hold
        self.assertEqual(frame.type, 1)
        self.assertFalse(frame.userdata.get("hold"))

    def test_hold_disabled_by_default(self):
        asr = make_asr(watermark_ms=0, hold_ms=0)
        asr.put_audio_frame(chunk(), {"status": "start"})
        asr.get_audio_frame()
        frame = asr.get_audio_frame()
        self.assertEqual(frame.type, 1)
        self.assertFalse(frame.userdata.get("hold"))

    def test_flush_epoch_stamped_and_bumped_on_flush(self):
        asr = make_asr(watermark_ms=0)
        asr.put_audio_frame(chunk(), {"status": "start"})
        frame = asr.get_audio_frame()
        self.assertEqual(frame.userdata.get("flush_epoch"), 0)
        asr.flush_talk()
        self.assertEqual(asr.flush_seq, 1)
        asr.put_audio_frame(chunk(), {"status": "start"})
        frame = asr.get_audio_frame()
        self.assertEqual(frame.userdata.get("flush_epoch"), 1)

    def test_flush_talk_resets_state(self):
        asr = make_asr(watermark_ms=300, hold_ms=1500)
        asr.put_audio_frame(chunk(), {"status": "start"})
        asr.put_audio_frame(chunk(), {"status": "end"})
        asr.get_audio_frame()
        asr.flush_talk()
        self.assertEqual(asr.queue.qsize(), 0)
        self.assertFalse(asr._response_active)
        self.assertEqual(asr._end_buffered, 0)
        frame = asr.get_audio_frame()  # no hold leakage after flush
        self.assertEqual(frame.type, 1)
        self.assertFalse(frame.userdata.get("hold"))


if __name__ == "__main__":
    unittest.main()
