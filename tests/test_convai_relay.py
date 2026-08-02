import asyncio
import base64
import json
import queue as thread_queue
import time
import unittest
import warnings
from unittest.mock import patch

import numpy as np
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp.web_exceptions import NotAppKeyWarning

import server.convai as convai
from server.routes import ELEVENLABS_SESSION_TTL_SECONDS, elevenlabs_audio_stream
from server.session_manager import session_manager


class FakeASR:
    def __init__(self):
        self.queue = thread_queue.Queue()


class FakeAvatar:
    sample_rate = 16000
    chunk = 320

    def __init__(self):
        self.frames = []
        self.flush_calls = 0
        self.msgqueues = []
        self.asr = FakeASR()

    def put_audio_frame(self, frame, eventpoint):
        self.frames.append((frame.copy(), dict(eventpoint)))

    def flush_talk(self):
        self.flush_calls += 1

    def add_msgqueue(self, q):
        self.msgqueues.append(q)


async def fake_signed_url(request):
    return web.json_response({"signed_url": f"ws://{request.host}/fake-convai"})


async def fake_convai(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    request.app["convai_ws"] = ws
    request.app["convai_connected"].set()
    async for message in ws:
        if message.type == WSMsgType.TEXT:
            request.app["received"].append(json.loads(message.data))
    request.app["convai_closed"].set()
    return ws


class ConvAIRelayTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.avatar = FakeAvatar()
        session_manager.add_session("convai-test", self.avatar)

        upstream_app = web.Application()
        upstream_app["received"] = []
        upstream_app["convai_connected"] = asyncio.Event()
        upstream_app["convai_closed"] = asyncio.Event()
        upstream_app.router.add_get(
            "/convai/conversation/get-signed-url", fake_signed_url)
        upstream_app.router.add_get("/fake-convai", fake_convai)
        self.upstream_app = upstream_app
        self.upstream_server = TestServer(upstream_app)
        await self.upstream_server.start_server()

        engine_app = web.Application()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotAppKeyWarning)
            engine_app["elevenlabs_control_sessions"] = {
                "test-token": {
                    "sessionid": "convai-test",
                    "generation": 0,
                    "expires_at": time.monotonic() + ELEVENLABS_SESSION_TTL_SECONDS,
                },
            }
        self.control = engine_app["elevenlabs_control_sessions"]["test-token"]
        engine_app.router.add_get("/api/elevenlabs/stream", elevenlabs_audio_stream)
        self.client = TestClient(TestServer(engine_app))
        await self.client.start_server()

        base_url = f"http://127.0.0.1:{self.upstream_server.port}"
        self.env = patch.dict("os.environ", {
            "ELEVENLABS_API_KEY": "test-key",
            "ELEVENLABS_AGENT_ID": "test-agent",
            "ELEVENLABS_BASE_URL": base_url,
        })
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        await self.client.close()
        await self.upstream_server.close()
        session_manager.remove_session("convai-test")

    async def open_conversation(self, language="id"):
        ws = await self.client.ws_connect("/api/elevenlabs/stream")
        await ws.send_json({
            "type": "init", "token": "test-token",
            "mode": "conversation", "language": language,
        })
        await asyncio.wait_for(
            self.upstream_app["convai_connected"].wait(), timeout=5)
        upstream_ws = self.upstream_app["convai_ws"]
        await upstream_ws.send_json({
            "type": "conversation_initiation_metadata",
            "conversation_initiation_metadata_event": {
                "agent_output_audio_format": "pcm_16000",
                "user_input_audio_format": "pcm_16000",
            },
        })
        ready = await asyncio.wait_for(ws.receive_json(), timeout=5)
        return ws, upstream_ws, ready

    async def send_audio(self, upstream_ws, pcm_bytes):
        await upstream_ws.send_json({
            "type": "audio",
            "audio_event": {"audio_base_64": base64.b64encode(pcm_bytes).decode()},
        })

    async def wait_frames(self, count, timeout=2.0):
        deadline = time.monotonic() + timeout
        while len(self.avatar.frames) < count and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        return self.avatar.frames

    async def collect_until(self, ws, message_type, timeout=3.0):
        received = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(deadline - time.monotonic(), 0.01)
            data = await asyncio.wait_for(ws.receive_json(), timeout=remaining)
            received.append(data)
            if data.get("type") == message_type:
                return received
        raise AssertionError(f"did not receive {message_type}: {received}")

    async def test_handshake_forwards_language_and_reports_ready(self):
        ws, upstream_ws, ready = await self.open_conversation(language="ms")
        self.assertEqual(ready["type"], "ready")
        self.assertEqual(ready["sample_rate"], 16000)
        self.assertEqual(ready["generation"], 0)
        init = self.upstream_app["received"][0]
        self.assertEqual(init["type"], "conversation_initiation_client_data")
        self.assertEqual(
            init["conversation_config_override"]["agent"]["language"], "ms")
        await ws.close()

    async def test_audio_events_become_one_response_with_start_end(self):
        ws, upstream_ws, _ = await self.open_conversation()
        pcm = np.arange(960, dtype="<i2").tobytes()
        await self.send_audio(upstream_ws, pcm[:700])
        await self.send_audio(upstream_ws, pcm[700:])
        await upstream_ws.send_json({"type": "agent_response_complete"})

        frames = await self.wait_frames(3)
        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0][1]["status"], "start")
        self.assertNotIn("status", frames[1][1])
        self.assertEqual(frames[2][1]["status"], "end")
        self.assertTrue(all(f[1]["generation"] == 0 for f in frames))
        np.testing.assert_allclose(
            np.concatenate([frame for frame, _ in frames]),
            np.arange(960, dtype=np.float32) / 32768.0,
        )
        states = await self.collect_until(ws, "state")
        self.assertEqual(states[-1], {"type": "state", "state": "speaking"})
        await ws.close()

    async def test_interruption_flushes_and_bumps_generation(self):
        ws, upstream_ws, _ = await self.open_conversation()
        await self.send_audio(upstream_ws, bytes(1280))  # 2 frames: 1 emitted, 1 pending
        await self.wait_frames(1)
        await upstream_ws.send_json({"type": "interruption"})

        received = await self.collect_until(ws, "interrupted")
        self.assertEqual(received[-1]["generation"], 1)
        self.assertEqual(self.control["generation"], 1)
        self.assertGreaterEqual(self.avatar.flush_calls, 1)
        # pending frame was aborted: no 'end' emitted
        self.assertTrue(all(f[1].get("status") != "end" for f in self.avatar.frames))
        await ws.close()

    async def test_user_transcript_during_response_is_barge_in(self):
        ws, upstream_ws, _ = await self.open_conversation()
        await self.send_audio(upstream_ws, bytes(1280))
        await self.wait_frames(1)
        await upstream_ws.send_json({
            "type": "user_transcript",
            "user_transcription_event": {"user_transcript": "halo"},
        })
        await self.collect_until(ws, "user_transcript")
        self.assertEqual(self.control["generation"], 1)
        self.assertGreaterEqual(self.avatar.flush_calls, 1)
        await ws.close()

    async def test_mic_binary_forwarded_as_user_audio_chunk(self):
        ws, upstream_ws, _ = await self.open_conversation()
        mic = bytes(range(64))
        await ws.send_bytes(mic)
        for _ in range(100):
            chunks = [m for m in self.upstream_app["received"]
                      if "user_audio_chunk" in m]
            if chunks:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(chunks)
        self.assertEqual(base64.b64decode(chunks[0]["user_audio_chunk"]), mic)
        await ws.close()

    async def test_user_message_interrupts_then_forwards(self):
        ws, upstream_ws, _ = await self.open_conversation()
        await self.send_audio(upstream_ws, bytes(1280))
        await self.wait_frames(1)
        await ws.send_json({"type": "user_message", "text": "jelaskan layanan"})
        for _ in range(100):
            texts = [m for m in self.upstream_app["received"]
                     if m.get("type") == "user_message"]
            if texts:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(texts)
        self.assertEqual(texts[0]["text"], "jelaskan layanan")
        self.assertEqual(self.control["generation"], 1)
        await ws.close()

    async def test_upstream_ping_gets_pong(self):
        ws, upstream_ws, _ = await self.open_conversation()
        await upstream_ws.send_json({"type": "ping", "ping_event": {"event_id": 7}})
        for _ in range(100):
            pongs = [m for m in self.upstream_app["received"]
                     if m.get("type") == "pong"]
            if pongs:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(pongs)
        self.assertEqual(pongs[0]["event_id"], 7)
        await ws.close()

    async def test_upstream_close_notifies_browser(self):
        ws, upstream_ws, _ = await self.open_conversation()
        await upstream_ws.close()
        received = await self.collect_until(ws, "closed")
        self.assertEqual(received[-1]["reason"], "upstream_closed")
        await ws.close()

    async def test_watchdog_finishes_response_without_complete(self):
        with patch.object(convai, "RESPONSE_IDLE_FLUSH_SECONDS", 0.3):
            ws, upstream_ws, _ = await self.open_conversation()
            await self.send_audio(upstream_ws, bytes(1280))
            await self.wait_frames(1)
            frames = await self.wait_frames(2, timeout=2.0)
            self.assertEqual(frames[-1][1].get("status"), "end")
            await ws.close()

    async def test_signed_url_failure_reports_error(self):
        with patch.dict("os.environ", {"ELEVENLABS_API_KEY": ""}):
            ws = await self.client.ws_connect("/api/elevenlabs/stream")
            await ws.send_json({
                "type": "init", "token": "test-token", "mode": "conversation",
            })
            error = await asyncio.wait_for(ws.receive_json(), timeout=5)
            self.assertEqual(error["type"], "error")
            self.assertEqual(error["code"], "signed_url_failed")
            await ws.close()


if __name__ == "__main__":
    unittest.main()
