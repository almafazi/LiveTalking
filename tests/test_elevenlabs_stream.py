import asyncio
import time
import unittest
import warnings

import numpy as np
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp.web_exceptions import NotAppKeyWarning

from server.routes import (
    ELEVENLABS_SESSION_TTL_SECONDS,
    elevenlabs_audio_stream,
)
from server.session_manager import session_manager


class FakeAvatar:
    sample_rate = 16000
    chunk = 320

    def __init__(self):
        self.frames = []

    def put_audio_frame(self, frame, eventpoint):
        self.frames.append((frame.copy(), dict(eventpoint)))


class ElevenLabsAudioStreamTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.avatar = FakeAvatar()
        session_manager.add_session("stream-test", self.avatar)
        self.app = web.Application()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotAppKeyWarning)
            self.app["elevenlabs_control_sessions"] = {
                "test-token": {
                    "sessionid": "stream-test",
                    "generation": 0,
                    "expires_at": time.monotonic() + ELEVENLABS_SESSION_TTL_SECONDS,
                },
            }
        self.app.router.add_get("/api/elevenlabs/stream", elevenlabs_audio_stream)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        session_manager.remove_session("stream-test")

    async def test_pcm_messages_form_one_continuous_response(self):
        ws = await self.client.ws_connect("/api/elevenlabs/stream")
        await ws.send_json({"type": "init", "token": "test-token"})
        ready = await ws.receive_json()
        self.assertEqual(ready, {"type": "ready", "sample_rate": 16000})

        await ws.send_json({"type": "start", "generation": 0, "sample_rate": 16000})
        pcm = np.arange(640, dtype="<i2").tobytes()
        await ws.send_bytes(pcm[:173])
        await ws.send_bytes(pcm[173:911])
        await ws.send_bytes(pcm[911:])
        await ws.send_json({"type": "end", "generation": 0})

        for _ in range(50):
            if len(self.avatar.frames) == 2:
                break
            await asyncio.sleep(0.01)

        self.assertEqual(len(self.avatar.frames), 2)
        self.assertEqual(self.avatar.frames[0][1]["status"], "start")
        self.assertEqual(self.avatar.frames[1][1]["status"], "end")
        self.assertEqual(self.avatar.frames[0][1]["generation"], 0)
        np.testing.assert_allclose(
            np.concatenate([frame for frame, _ in self.avatar.frames]),
            np.arange(640, dtype=np.float32) / 32768.0,
        )
        await ws.close()

    async def test_rejects_wrong_sample_rate_without_audio(self):
        ws = await self.client.ws_connect("/api/elevenlabs/stream")
        await ws.send_json({"type": "init", "token": "test-token"})
        await ws.receive_json()
        await ws.send_json({"type": "start", "generation": 0, "sample_rate": 24000})
        error = await ws.receive_json()

        self.assertEqual(error["type"], "error")
        self.assertEqual(error["code"], "unsupported_sample_rate")
        self.assertEqual(self.avatar.frames, [])
        await ws.close()


if __name__ == "__main__":
    unittest.main()
