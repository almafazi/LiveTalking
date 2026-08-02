###############################################################################
#  服务端 ElevenLabs ConvAI 中继
#
#  引擎自己连接 ConvAI WebSocket：浏览器只上行麦克风 PCM、下行轻量状态事件，
#  代理音频不再经过用户网络（旧路径 ElevenLabs→浏览器→Laravel→引擎是卡顿根因）。
###############################################################################

import asyncio
import base64
import json
import os
import queue as thread_queue
import time

import aiohttp
import numpy as np

from utils.logger import logger

SESSION_TTL_SECONDS = 2 * 60 * 60
UPSTREAM_MAX_MSG_BYTES = 4 * 1024 * 1024
METADATA_TIMEOUT_SECONDS = 10
# 上游停止送音但没有 agent_response_complete 时，多久后强制收尾（对应旧客户端
# AVATAR_IDLE_FLUSH_MS；也是 hold-last-pose 不会冻住的保障之一）
RESPONSE_IDLE_FLUSH_SECONDS = 1.2
# asr 队列的软上限（帧数，60s）：超长独白时暂缓读上游，而不是丢音频
ASR_QUEUE_SOFT_LIMIT = 3000


# 防止后台清理 task 被垃圾回收（asyncio 只持弱引用）
_pending_closes = set()


class ConvAIError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


async def fetch_signed_url() -> str:
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    agent_id = os.environ.get("ELEVENLABS_AGENT_ID", "")
    if not api_key or not agent_id:
        raise ConvAIError(
            "signed_url_failed",
            "Missing ELEVENLABS_API_KEY or ELEVENLABS_AGENT_ID",
        )
    base_url = os.environ.get(
        "ELEVENLABS_BASE_URL", "https://api.elevenlabs.io/v1"
    ).rstrip("/")
    url = f"{base_url}/convai/conversation/get-signed-url?agent_id={agent_id}"
    timeout = aiohttp.ClientTimeout(total=15, sock_connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers={"xi-api-key": api_key}) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logger.error("ConvAI signed-url failed: %s %s", resp.status, text)
                raise ConvAIError(
                    "signed_url_failed", f"ElevenLabs error {resp.status}"
                )
            data = json.loads(text)
    signed_url = data.get("signed_url")
    if not signed_url:
        raise ConvAIError(
            "signed_url_failed", "ElevenLabs response did not contain signed_url"
        )
    return signed_url


class ResponseFramer:
    """把一段 ConvAI PCM 回复切成 20ms 帧送入 avatar，start/end 事件与旧
    /api/elevenlabs/stream 处理器语义一致：扣一帧不发，好让 end 落在真正的末帧。"""

    def __init__(self, avatar_session, generation: int):
        self.avatar_session = avatar_session
        self.generation = generation
        self.pending = None
        self.remainder = bytearray()
        self.started = False
        self.messages = 0
        self.payload_bytes = 0
        self.started_at = time.monotonic()

    def _put(self, frame, end=False):
        eventpoint = {"generation": self.generation}
        if not self.started:
            eventpoint["status"] = "start"
            self.started = True
        if end:
            eventpoint["status"] = "end"
        self.avatar_session.put_audio_frame(frame, eventpoint)

    def feed(self, payload: bytes):
        self.messages += 1
        self.payload_bytes += len(payload)
        self.remainder.extend(payload)
        frame_bytes = self.avatar_session.chunk * 2
        while len(self.remainder) >= frame_bytes:
            raw_frame = bytes(self.remainder[:frame_bytes])
            del self.remainder[:frame_bytes]
            frame = np.frombuffer(raw_frame, dtype="<i2").astype(np.float32) / 32768.0
            if self.pending is not None:
                self._put(self.pending)
            self.pending = frame

    def finish(self):
        if self.remainder:
            even_bytes = len(self.remainder) - (len(self.remainder) % 2)
            raw_tail = bytes(self.remainder[:even_bytes])
            if raw_tail:
                tail = np.frombuffer(raw_tail, dtype="<i2").astype(np.float32) / 32768.0
                padded = np.zeros(self.avatar_session.chunk, dtype=np.float32)
                padded[:min(tail.shape[0], padded.shape[0])] = tail[:padded.shape[0]]
                if self.pending is not None:
                    self._put(self.pending)
                self.pending = padded
            self.remainder = bytearray()
        if self.pending is not None:
            self._put(self.pending, end=True)
            self.pending = None
        elapsed = max(time.monotonic() - self.started_at, 1e-6)
        samples = self.payload_bytes // 2
        logger.info(
            "ConvAI response complete generation=%s messages=%s samples=%s (%.1fx real-time)",
            self.generation, self.messages, samples,
            (samples / self.avatar_session.sample_rate) / elapsed,
        )

    def abort(self):
        self.pending = None
        self.remainder = bytearray()


class TurnTimer:
    """Per-giliran: catat tiap tahap dari user selesai bicara sampai avatar
    benar-benar terdengar, lalu keluarkan satu baris ringkasan yang greppable.

    grep 'LATENCY' livetalking.log
    """

    def __init__(self, generation: int, trigger: str):
        self.generation = generation
        self.trigger = trigger          # 'voice' | 'text' | 'greeting'
        self.t_start = time.monotonic()  # user turn selesai (transcript/kirim teks)
        # catatan: event teks lengkap sering tiba SETELAH audio pertama —
        # jadi agent_text_ms bukan "waktu LLM", pakai tts_first_ms untuk itu
        self.t_agent_text = None
        self.t_first_audio = None        # byte TTS pertama tiba di engine
        self.t_ingest_done = None        # seluruh audio jawaban sudah masuk
        self.t_playout_start = None      # frame pertama benar-benar dikirim ke WebRTC
        self.samples = 0
        self.summarized = False

    def _ms(self, value):
        return "-" if value is None else f"{(value - self.t_start) * 1000:.0f}"

    def log_playout_start(self):
        self.t_playout_start = time.monotonic()
        logger.info(
            "LATENCY turn gen=%s trigger=%s agent_text_ms=%s tts_first_ms=%s "
            "playout_ms=%s pipeline_ms=%s",
            self.generation, self.trigger,
            self._ms(self.t_agent_text), self._ms(self.t_first_audio),
            self._ms(self.t_playout_start),
            "-" if self.t_first_audio is None
            else f"{(self.t_playout_start - self.t_first_audio) * 1000:.0f}",
        )

    def log_summary(self, reason: str):
        if self.summarized:
            return
        self.summarized = True
        audio_seconds = self.samples / 16000.0
        ingest_span = (
            (self.t_ingest_done - self.t_first_audio)
            if self.t_first_audio and self.t_ingest_done else 0.0
        )
        rate = f"{audio_seconds / ingest_span:.1f}" if ingest_span > 0.01 else "-"
        logger.info(
            "LATENCY summary gen=%s trigger=%s reason=%s audio_s=%.1f "
            "ingest_rate=%sx agent_text_ms=%s tts_first_ms=%s playout_ms=%s total_ms=%s",
            self.generation, self.trigger, reason, audio_seconds, rate,
            self._ms(self.t_agent_text), self._ms(self.t_first_audio),
            self._ms(self.t_playout_start),
            f"{(time.monotonic() - self.t_start) * 1000:.0f}",
        )


class ConvAIRelay:
    def __init__(self, browser_ws, avatar_session, control, language: str):
        self.browser_ws = browser_ws
        self.avatar_session = avatar_session
        self.control = control
        self.language = language
        self.client_session = None
        self.upstream = None
        self.framer = None
        self.tasks = []
        self.msgqueue = None
        self.response_active = False
        self.last_audio = 0.0
        self.closed = False
        self.turn = None

    async def start(self):
        signed_url = await fetch_signed_url()
        self.client_session = aiohttp.ClientSession()
        try:
            self.upstream = await self.client_session.ws_connect(
                signed_url, heartbeat=15, max_msg_size=UPSTREAM_MAX_MSG_BYTES,
            )
        except Exception as exc:
            await self.client_session.close()
            self.client_session = None
            raise ConvAIError("upstream_connect_failed", f"ConvAI connect failed: {exc}")

        await self.upstream.send_json({
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {"agent": {"language": self.language}},
        })
        await self._await_metadata()
        await self._send_browser({
            "type": "ready",
            "sample_rate": self.avatar_session.sample_rate,
            "input_sample_rate": self.avatar_session.sample_rate,
            "generation": self.control["generation"],
        })
        self.msgqueue = thread_queue.Queue()
        self.avatar_session.add_msgqueue(self.msgqueue)
        self.tasks = [
            asyncio.create_task(self._upstream_reader()),
            asyncio.create_task(self._playout_notifier()),
            asyncio.create_task(self._watchdog()),
        ]
        logger.info("ConvAI relay started session=%s language=%s",
                    self.control.get("sessionid"), self.language)

    async def _await_metadata(self):
        deadline = time.monotonic() + METADATA_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConvAIError("upstream_connect_failed", "ConvAI metadata timed out")
            try:
                message = await asyncio.wait_for(self.upstream.receive(), timeout=remaining)
            except asyncio.TimeoutError:
                raise ConvAIError("upstream_connect_failed", "ConvAI metadata timed out")
            if message.type != aiohttp.WSMsgType.TEXT:
                raise ConvAIError("upstream_connect_failed", "ConvAI closed during init")
            try:
                data = json.loads(message.data)
            except (TypeError, ValueError):
                continue
            if data.get("type") == "ping":
                await self._pong(data)
                continue
            if data.get("type") != "conversation_initiation_metadata":
                continue
            meta = data.get("conversation_initiation_metadata_event") or {}
            output_format = str(meta.get("agent_output_audio_format") or "")
            expected = f"pcm_{self.avatar_session.sample_rate}"
            if output_format != expected:
                raise ConvAIError(
                    "unsupported_sample_rate",
                    f"Agent output must be {expected}, got {output_format or 'unknown'}",
                )
            return

    async def _pong(self, data):
        event_id = (data.get("ping_event") or {}).get("event_id")
        await self.upstream.send_json({"type": "pong", "event_id": event_id})

    async def _send_browser(self, payload: dict):
        if self.browser_ws.closed:
            return
        try:
            await self.browser_ws.send_json(payload)
        except (ConnectionResetError, RuntimeError):
            pass

    async def send_mic(self, pcm: bytes):
        if self.upstream is None or self.upstream.closed:
            return
        encoded = base64.b64encode(pcm).decode("ascii")
        try:
            await self.upstream.send_json({"user_audio_chunk": encoded})
        except ConnectionResetError:
            pass

    async def user_message(self, text: str):
        if not text:
            return
        await self.barge_in()
        if self.upstream is not None and not self.upstream.closed:
            await self.upstream.send_json({"type": "user_message", "text": text})
        self.turn = TurnTimer(self.control["generation"], "text")
        logger.info("LATENCY turn-start gen=%s trigger=text chars=%s",
                    self.control["generation"], len(text))
        await self._send_browser({"type": "state", "state": "thinking"})

    async def barge_in(self):
        if self.turn is not None:
            self.turn.log_summary("interrupted")
            self.turn = None
        self.control["generation"] += 1
        if self.framer is not None:
            self.framer.abort()
        self.response_active = False
        self.avatar_session.flush_talk()
        await self._send_browser({
            "type": "interrupted",
            "generation": self.control["generation"],
        })
        await self._send_browser({"type": "state", "state": "listening"})

    def _finish_response(self):
        if self.framer is not None:
            self.framer.finish()
            if self.turn is not None:
                self.turn.t_ingest_done = time.monotonic()
                self.turn.samples = self.framer.payload_bytes // 2
        self.response_active = False

    async def _upstream_reader(self):
        reason = "upstream_closed"
        try:
            async for message in self.upstream:
                if message.type != aiohttp.WSMsgType.TEXT:
                    if message.type in (aiohttp.WSMsgType.CLOSE,
                                        aiohttp.WSMsgType.CLOSED,
                                        aiohttp.WSMsgType.ERROR):
                        break
                    continue
                try:
                    data = json.loads(message.data)
                except (TypeError, ValueError):
                    continue
                event_type = data.get("type")
                if event_type == "ping":
                    await self._pong(data)
                elif event_type == "audio":
                    await self._handle_audio(data)
                elif event_type == "agent_response":
                    text = (data.get("agent_response_event") or {}).get("agent_response", "")
                    if self.turn is not None and self.turn.t_agent_text is None:
                        self.turn.t_agent_text = time.monotonic()
                    await self._send_browser({"type": "agent_response", "text": text})
                elif event_type == "user_transcript":
                    # 用户开口即打断：显式 interruption 事件迟到/缺失时的兜底
                    if self.response_active:
                        await self.barge_in()
                    text = (data.get("user_transcription_event") or {}).get("user_transcript", "")
                    self.turn = TurnTimer(self.control["generation"], "voice")
                    logger.info("LATENCY turn-start gen=%s trigger=voice chars=%s",
                                self.control["generation"], len(text))
                    await self._send_browser({"type": "user_transcript", "text": text})
                    await self._send_browser({"type": "state", "state": "thinking"})
                elif event_type == "interruption":
                    await self.barge_in()
                elif event_type in ("agent_response_complete", "agent_response_correction"):
                    if self.response_active:
                        self._finish_response()
                # 其余事件（agent_chat_response_part 等）静默忽略
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ConvAI upstream reader error:")
            reason = "upstream_error"
        # 上游断开：告知浏览器并关闭浏览器侧 WS，让路由 handler 收尾
        if self.response_active:
            self._finish_response()
        await self._send_browser({"type": "closed", "reason": reason})
        if not self.browser_ws.closed:
            try:
                await self.browser_ws.close()
            except (ConnectionResetError, RuntimeError):
                pass

    async def _handle_audio(self, data):
        b64 = (data.get("audio_event") or {}).get("audio_base_64", "")
        if not b64:
            return
        try:
            payload = base64.b64decode(b64)
        except (TypeError, ValueError):
            return
        if not self.response_active:
            self.framer = ResponseFramer(self.avatar_session, self.control["generation"])
            self.response_active = True
            if self.turn is None:
                # sapaan pembuka: tidak ada user_transcript, ukur dari audio pertama
                self.turn = TurnTimer(self.control["generation"], "greeting")
            if self.turn.t_first_audio is None:
                self.turn.t_first_audio = time.monotonic()
            await self._send_browser({"type": "state", "state": "speaking"})
        elif self.framer.generation != self.control["generation"]:
            return  # 打断后的残余音频
        self.framer.feed(payload)
        self.last_audio = time.monotonic()
        asr = getattr(self.avatar_session, "asr", None)
        if asr is not None and asr.queue.qsize() > ASR_QUEUE_SOFT_LIMIT:
            await asyncio.sleep(0.2)

    async def _playout_notifier(self):
        """把播放侧（WebRTC recv 时刻）的 start/end 事件转成浏览器状态，
        让 UI 的 speaking/listening 与真实播出对齐（复用 SSE 的 msgqueue 机制）。"""
        while True:
            try:
                raw = self.msgqueue.get_nowait()
            except thread_queue.Empty:
                await asyncio.sleep(0.05)
                continue
            try:
                eventpoint = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if eventpoint.get("generation") != self.control["generation"]:
                continue
            status = eventpoint.get("status")
            if status == "start":
                if self.turn is not None and self.turn.t_playout_start is None:
                    self.turn.log_playout_start()
                await self._send_browser({"type": "state", "state": "speaking"})
            elif status == "end":
                if self.turn is not None:
                    self.turn.log_summary("completed")
                    self.turn = None
                await self._send_browser({"type": "state", "state": "listening"})

    async def _watchdog(self):
        while True:
            await asyncio.sleep(0.25)
            if (self.response_active
                    and time.monotonic() - self.last_audio > RESPONSE_IDLE_FLUSH_SECONDS):
                logger.info("ConvAI response idle-flushed generation=%s",
                            self.control["generation"])
                self._finish_response()

    async def close(self, reason: str = "ended"):
        if self.closed:
            return
        self.closed = True
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            _, pending = await asyncio.wait(self.tasks, timeout=5)
            for task in pending:
                logger.warning("ConvAI relay task refused cancellation: %r", task)
        try:
            if self.framer is not None:
                self.framer.abort()
            self.avatar_session.flush_talk()
        except Exception:
            logger.exception("ConvAI relay flush error:")
        if self.msgqueue is not None:
            try:
                self.avatar_session.msgqueues.remove(self.msgqueue)
            except ValueError:
                pass
        if self.upstream is not None and not self.upstream.closed:
            try:
                await self.upstream.close()
            except Exception:
                pass
        if self.client_session is not None:
            await self.client_session.close()
        if not self.browser_ws.closed:
            await self._send_browser({"type": "closed", "reason": reason})
            try:
                await self.browser_ws.close()
            except (ConnectionResetError, RuntimeError):
                pass
        if self.turn is not None:
            self.turn.log_summary("relay_closed")
            self.turn = None
        logger.info("ConvAI relay closed session=%s reason=%s",
                    self.control.get("sessionid"), reason)


async def run_conversation(request, ws, control, avatar_session, params):
    """浏览器 WS init 携带 mode:'conversation' 时的完整会话处理。"""
    language = str(params.get("language") or "en")
    relay = ConvAIRelay(ws, avatar_session, control, language)
    try:
        await relay.start()
    except ConvAIError as exc:
        await ws.send_json({"type": "error", "code": exc.code, "message": exc.message})
        await ws.close()
        return ws
    except Exception:
        logger.exception("ConvAI relay start failed:")
        await ws.send_json({
            "type": "error", "code": "upstream_connect_failed",
            "message": "ConvAI connection failed",
        })
        await ws.close()
        return ws

    try:
        async for message in ws:
            control["expires_at"] = time.monotonic() + SESSION_TTL_SECONDS
            if message.type == aiohttp.WSMsgType.BINARY:
                await relay.send_mic(message.data)
                continue
            if message.type != aiohttp.WSMsgType.TEXT:
                if message.type in (aiohttp.WSMsgType.CLOSE,
                                    aiohttp.WSMsgType.CLOSED,
                                    aiohttp.WSMsgType.ERROR):
                    break
                continue
            try:
                data = json.loads(message.data)
            except (TypeError, ValueError):
                continue
            message_type = data.get("type")
            if message_type == "user_message":
                await relay.user_message(str(data.get("text", "")))
            elif message_type == "interrupt":
                await relay.barge_in()
            elif message_type == "end":
                break
    except ConnectionResetError:
        logger.info("ConvAI browser stream disconnected")
    finally:
        # handler 可能在关闭途中被取消（客户端掉线/服务停机）；把清理放进独立
        # task 并 shield，保证上游连接与 ClientSession 一定被释放
        close_task = asyncio.create_task(relay.close("ended"))
        _pending_closes.add(close_task)
        close_task.add_done_callback(_pending_closes.discard)
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            raise
    return ws
