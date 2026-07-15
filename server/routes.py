###############################################################################
#  服务器路由 — 统一异常处理的 API 路由
###############################################################################

import json
import os
import asyncio
import hmac
import ipaddress
import secrets
import time
from urllib.parse import urlsplit
import aiohttp
from aiohttp import web

from utils.logger import logger


# ─── 路由工具函数 ──────────────────────────────────────────────────────────

def json_ok(data=None):
    """返回成功 JSON 响应"""
    body = {"code": 0, "msg": "ok"}
    if data is not None:
        body["data"] = data
    return web.Response(
        content_type="application/json",
        text=json.dumps(body),
    )


def json_error(msg: str, code: int = -1, status: int = 200):
    """返回错误 JSON 响应"""
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps({"code": code, "msg": str(msg)}),
    )


from server.session_manager import session_manager
from server.avatar_routes import setup_avatar_routes

def get_session(request, sessionid: str):
    """从 app 中获取 session 实例"""
    return session_manager.get_session(sessionid)


ELEVENLABS_SESSION_TTL_SECONDS = 2 * 60 * 60
ELEVENLABS_MAX_AUDIO_BYTES = 2 * 1024 * 1024


def _is_loopback_request(request) -> bool:
    try:
        remote_is_loopback = ipaddress.ip_address(request.remote or "").is_loopback
    except ValueError:
        return False
    host = (request.host or "").lower()
    host_is_loopback = (
        host == "localhost" or host.startswith("localhost:") or
        host == "127.0.0.1" or host.startswith("127.0.0.1:") or
        host == "[::1]" or host.startswith("[::1]:")
    )
    return remote_is_loopback and host_is_loopback


def _check_elevenlabs_access(request):
    """Protect credit-minting endpoints on public deployments.

    Localhost remains convenient for development. Remote access requires an
    explicit shared secret so a public Vast.ai port cannot mint signed URLs.
    """
    origin = request.headers.get("Origin", "")
    if origin:
        origin_host = urlsplit(origin).netloc.lower()
        if not origin_host or origin_host != (request.host or "").lower():
            return json_error("Cross-origin ElevenLabs access is not allowed", status=403)

    expected = os.environ.get("ELEVENLABS_ACCESS_TOKEN", "").strip()
    if not expected:
        if _is_loopback_request(request):
            return None
        return json_error(
            "Remote ElevenLabs access is disabled. Set ELEVENLABS_ACCESS_TOKEN on the server.",
            status=503,
        )

    supplied = request.headers.get("X-ElevenLabs-Access-Token", "")
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        return json_error("Invalid ElevenLabs access token", status=401)
    return None


def _elevenlabs_sessions(app):
    sessions = app.setdefault("elevenlabs_control_sessions", {})
    now = time.monotonic()
    expired = [token for token, state in sessions.items() if state["expires_at"] <= now]
    for token in expired:
        sessions.pop(token, None)
    return sessions


def _get_elevenlabs_control_session(request):
    token = request.headers.get("X-LiveTalking-Token", "")
    state = _elevenlabs_sessions(request.app).get(token)
    if not token or state is None:
        return None, json_error("Invalid or expired conversation token", status=401)
    state["expires_at"] = time.monotonic() + ELEVENLABS_SESSION_TTL_SECONDS
    return state, None


def _check_runtime_manager_access(request):
    expected = os.environ.get("RUNTIME_MANAGER_TOKEN", "").strip()
    auth = request.headers.get("Authorization", "")
    supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not expected:
        return json_error("RUNTIME_MANAGER_TOKEN is not configured", status=503)
    if not supplied or not hmac.compare_digest(supplied, expected):
        return json_error("Invalid runtime manager token", status=401)
    return None


# ─── 路由处理函数 ──────────────────────────────────────────────────────────

async def human(request):
    """文本输入（echo/chat 模式），支持 voice/emotion 参数"""
    try:
        params: dict = await request.json()

        sessionid: str = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")

        if params.get('interrupt'):
            avatar_session.flush_talk()

        datainfo = {}
        if params.get('tts'):  # tts 参数透传（voice, emotion 等）
            datainfo['tts'] = params.get('tts')

        if params['type'] == 'echo':
            avatar_session.put_msg_txt(params['text'], datainfo)
        elif params['type'] == 'chat':
            llm_response = request.app.get("llm_response")
            if llm_response:
                asyncio.get_event_loop().run_in_executor(
                    None, llm_response, params['text'], avatar_session, datainfo
                )

        return json_ok()
    except Exception as e:
        logger.exception('human route exception:')
        return json_error(str(e))


async def interrupt_talk(request):
    """打断当前说话"""
    try:
        params = await request.json()
        sessionid = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")
        avatar_session.flush_talk()
        return json_ok()
    except Exception as e:
        logger.exception('interrupt_talk exception:')
        return json_error(str(e))


async def humanaudio(request):
    """上传音频文件"""
    try:
        form = await request.post()
        sessionid = str(form.get('sessionid', ''))
        fileobj = form["file"]
        filebytes = fileobj.file.read()

        datainfo = {}

        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")
        avatar_session.put_audio_file(filebytes, datainfo)
        return json_ok()
    except Exception as e:
        logger.exception('humanaudio exception:')
        return json_error(str(e))


async def elevenlabs_audio(request):
    """Upload one protected, ordered ElevenLabs audio slice."""
    try:
        control, error = _get_elevenlabs_control_session(request)
        if error is not None:
            return error

        form = await request.post()
        try:
            generation = int(form.get("generation", "-1"))
        except (TypeError, ValueError):
            return json_error("Invalid audio generation", status=400)
        if generation != control["generation"]:
            return json_error("Stale audio generation", code=-2, status=409)

        fileobj = form.get("file")
        if fileobj is None or not hasattr(fileobj, "file"):
            return json_error("Missing audio file", status=400)
        filebytes = fileobj.file.read(ELEVENLABS_MAX_AUDIO_BYTES + 1)
        if len(filebytes) > ELEVENLABS_MAX_AUDIO_BYTES:
            return json_error("Audio slice is too large", status=413)

        avatar_session = get_session(request, control["sessionid"])
        if avatar_session is None:
            return json_error("session not found", status=404)
        avatar_session.put_audio_file(filebytes, {"generation": generation})
        return json_ok()
    except Exception as e:
        logger.exception('elevenlabs_audio exception:')
        return json_error(str(e), status=500)


async def elevenlabs_interrupt(request):
    """Advance the audio generation before flushing stale avatar speech."""
    try:
        control, error = _get_elevenlabs_control_session(request)
        if error is not None:
            return error
        params = await request.json()
        try:
            generation = int(params.get("generation", -1))
        except (TypeError, ValueError):
            return json_error("Invalid audio generation", status=400)
        if generation < control["generation"]:
            return json_ok(data={"generation": control["generation"], "stale": True})
        control["generation"] = generation

        avatar_session = get_session(request, control["sessionid"])
        if avatar_session is None:
            return json_error("session not found", status=404)
        avatar_session.flush_talk()
        return json_ok()
    except Exception as e:
        logger.exception('elevenlabs_interrupt exception:')
        return json_error(str(e), status=500)


async def elevenlabs_end(request):
    """Revoke a browser conversation token and stop its queued speech."""
    token = request.headers.get("X-LiveTalking-Token", "")
    control, error = _get_elevenlabs_control_session(request)
    if error is not None:
        return error
    _elevenlabs_sessions(request.app).pop(token, None)
    avatar_session = get_session(request, control["sessionid"])
    if avatar_session is not None:
        avatar_session.flush_talk()
    return json_ok()


async def elevenlabs_signed_url(request):
    """获取 ElevenLabs ConvAI signed URL（API key 保留在服务端）"""
    try:
        access_error = _check_elevenlabs_access(request)
        if access_error is not None:
            return access_error

        api_key = os.environ.get("ELEVENLABS_API_KEY", "")
        agent_id = os.environ.get("ELEVENLABS_AGENT_ID", "")
        if not api_key or not agent_id:
            return json_error(
                "Missing ELEVENLABS_API_KEY or ELEVENLABS_AGENT_ID",
                status=503,
            )

        sessionid = str(request.query.get("sessionid", "0"))
        if get_session(request, sessionid) is None:
            return json_error("session not found", status=404)

        base_url = os.environ.get(
            "ELEVENLABS_BASE_URL", "https://api.elevenlabs.io/v1"
        ).rstrip("/")
        url = f"{base_url}/convai/conversation/get-signed-url?agent_id={agent_id}"
        timeout = aiohttp.ClientTimeout(total=15, sock_connect=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers={"xi-api-key": api_key}) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    logger.error("ElevenLabs signed-url failed: %s %s", resp.status, text)
                    return json_error(f"ElevenLabs error {resp.status}: {text}", status=502)
                data = json.loads(text)
        signed_url = data.get("signed_url")
        if not signed_url:
            return json_error("ElevenLabs response did not contain signed_url", status=502)

        control_token = secrets.token_urlsafe(32)
        _elevenlabs_sessions(request.app)[control_token] = {
            "sessionid": sessionid,
            "generation": 0,
            "expires_at": time.monotonic() + ELEVENLABS_SESSION_TTL_SECONDS,
        }
        return web.json_response({
            "signedUrl": signed_url,
            "controlToken": control_token,
            "generation": 0,
        }, headers={"Cache-Control": "no-store"})
    except Exception as e:
        logger.exception('elevenlabs_signed_url exception:')
        return json_error(str(e), status=500)


async def create_audio_session(request):
    """Mint a short-lived avatar-audio token without exposing provider secrets."""
    access_error = _check_runtime_manager_access(request)
    if access_error is not None:
        return access_error

    try:
        params = await request.json() if request.can_read_body else {}
    except Exception:
        params = {}
    sessionid = str(params.get("sessionid", "0"))
    if get_session(request, sessionid) is None:
        return json_error("session not found", status=404)

    control_token = secrets.token_urlsafe(32)
    _elevenlabs_sessions(request.app)[control_token] = {
        "sessionid": sessionid,
        "generation": 0,
        "expires_at": time.monotonic() + ELEVENLABS_SESSION_TTL_SECONDS,
    }
    return web.json_response({
        "control_token": control_token,
        "generation": 0,
        "expires_in": ELEVENLABS_SESSION_TTL_SECONDS,
    }, headers={"Cache-Control": "no-store"})


async def set_audiotype(request):
    """设置自定义状态（动作编排）"""
    try:
        params = await request.json()
        sessionid = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")
        avatar_session.set_custom_state(params['audiotype'])
        return json_ok()
    except Exception as e:
        logger.exception('set_audiotype exception:')
        return json_error(str(e))


async def record(request):
    """录制控制"""
    try:
        params = await request.json()
        sessionid = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")
        if params['type'] == 'start_record':
            avatar_session.start_recording()
        elif params['type'] == 'end_record':
            avatar_session.stop_recording()
        return json_ok()
    except Exception as e:
        logger.exception('record exception:')
        return json_error(str(e))


async def is_speaking(request):
    """查询是否正在说话"""
    params = await request.json()
    sessionid = params.get('sessionid', '')
    avatar_session = get_session(request, sessionid)
    if avatar_session is None:
        return json_error("session not found")
    return json_ok(data=avatar_session.is_speaking())


async def admin_config(request):
    """Admin: 获取全局配置参数"""
    try:
        opt = request.app.get("opt")
        if opt:
            return json_ok(data={"config": vars(opt)})
        return json_error("Config not found")
    except Exception as e:
        logger.exception('admin_config exception:')
        return json_error(str(e))


async def admin_sessions(request):
    """Admin: 获取活跃的会话及其配置"""
    try:
        sessions_info = []
        for sid, avatar_session in session_manager.sessions.items():
            if avatar_session:
                s_opt = getattr(avatar_session, 'opt', None)
                s_data = {
                    "sessionid": sid,
                    "speaking": avatar_session.is_speaking() if hasattr(avatar_session, 'is_speaking') else False,
                    "recording": getattr(avatar_session, 'recording', False),
                }
                if s_opt:
                    s_data.update({
                        "model": getattr(s_opt, "model", ""),
                        "avatar_id": getattr(s_opt, "avatar_id", ""),
                        "REF_FILE": getattr(s_opt, "REF_FILE", ""),
                        "transport": getattr(s_opt, "transport", ""),
                        "batch_size": getattr(s_opt, "batch_size", 0),
                        "customopt": getattr(s_opt, "customopt", []),
                    })
                sessions_info.append(s_data)
        return json_ok(data={"sessions": sessions_info})
    except Exception as e:
        logger.exception('admin_sessions exception:')
        return json_error(str(e))


# ─── 路由注册 ──────────────────────────────────────────────────────────────

def setup_routes(app):
    """注册所有路由到 aiohttp app"""
    app.router.add_post("/human", human)
    app.router.add_post("/humanaudio", humanaudio)
    app.router.add_post("/set_audiotype", set_audiotype)
    app.router.add_post("/record", record)
    app.router.add_post("/interrupt_talk", interrupt_talk)
    app.router.add_post("/is_speaking", is_speaking)
    app.router.add_get("/api/elevenlabs/signed-url", elevenlabs_signed_url)
    app.router.add_post("/api/audio/session", create_audio_session)
    app.router.add_post("/api/elevenlabs/audio", elevenlabs_audio)
    app.router.add_post("/api/elevenlabs/interrupt", elevenlabs_interrupt)
    app.router.add_post("/api/elevenlabs/end", elevenlabs_end)
    app.router.add_get("/api/admin/config", admin_config)
    app.router.add_get("/api/admin/sessions", admin_sessions)

    # ── Local ASR endpoint (SenseVoice/FunASR) ── Issue #604 ──
    try:
        from server.asr_server import asr_websocket_handler, is_funasr_available
        if is_funasr_available():
            app.router.add_get("/api/asr", asr_websocket_handler)
            logger.info("[ASR] Local SenseVoice ASR endpoint enabled at /api/asr")
        else:
            logger.info("[ASR] funasr not installed — local ASR endpoint disabled "
                        "(pip install funasr modelscope)")
    except Exception as e:
        logger.warning(f"[ASR] Failed to register ASR endpoint: {e}")

    # 注册 avatar 生成相关的路由
    setup_avatar_routes(app)

    # ── SRS HTTP-FLV 同源代理 ──────────────────────────────────────────────
    # 浏览器通过同源 /srs-live/... 播放 SRS 的 HTTP-FLV（走 TCP），
    # 适配 vast.ai 等只暴露少量 TCP 端口、UDP 不可靠的环境。
    srs_http_port = int(os.environ.get("SRS_HTTP_PORT", "8088"))

    async def srs_flv_proxy(request):
        path = request.match_info.get("path", "")
        url = f"http://127.0.0.1:{srs_http_port}/{path}"
        if request.query_string:
            url = f"{url}?{request.query_string}"
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status >= 400:
                        return web.Response(status=resp.status, body=await resp.read())
                    out = web.StreamResponse(
                        status=200,
                        headers={
                            "Content-Type": resp.headers.get("Content-Type", "video/x-flv"),
                            "Cache-Control": "no-cache, no-store",
                            "Access-Control-Allow-Origin": "*",
                        },
                    )
                    await out.prepare(request)
                    try:
                        async for chunk in resp.content.iter_chunked(64 * 1024):
                            await out.write(chunk)
                    except (ConnectionResetError, asyncio.CancelledError):
                        pass  # 客户端断开，正常结束
                    return out
        except aiohttp.ClientError as e:
            logger.error("FLV proxy connect failed: %s", e)
            return web.Response(status=502, text=f"SRS FLV unavailable: {e}")

    app.router.add_get("/srs-live/{path:.*}", srs_flv_proxy)

    # ── SRS WHEP 同源信令代理（低延迟 WebRTC-over-TCP 播放）─────────────────
    # 浏览器 POST offer SDP 到同源 /srs-whep/...，由本服务转发给 SRS 的
    # http_api（默认 10100）。可选注入 eip，让 SRS answer 里通告
    # 公网 IP:外部TCP端口 的候选，使浏览器能走 vast 映射的 TCP 端口连上。
    srs_api_port = int(os.environ.get("SRS_API_PORT", "10100"))
    srs_rtc_eip = os.environ.get("SRS_RTC_EIP", "").strip()  # 形如 "1.2.3.4:34567"
    if srs_rtc_eip:
        logger.info("WHEP proxy will advertise SRS RTC TCP candidate %s", srs_rtc_eip)
    else:
        logger.warning(
            "SRS_RTC_EIP is not set; WHEP relies on the candidate generated by SRS"
        )

    async def srs_whep_proxy(request):
        path = request.match_info.get("path", "")
        url = f"http://127.0.0.1:{srs_api_port}/{path}"
        query = request.query_string
        if srs_rtc_eip:
            query = f"{query}&eip={srs_rtc_eip}" if query else f"eip={srs_rtc_eip}"
        if query:
            url = f"{url}?{query}"
        offer_sdp = await request.read()
        timeout = aiohttp.ClientTimeout(total=15, sock_connect=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url, data=offer_sdp,
                    headers={"Content-Type": "application/sdp"},
                ) as resp:
                    body = await resp.read()
                    logger.info(
                        "WHEP signaling status=%s answer_len=%s eip=%s",
                        resp.status,
                        len(body),
                        srs_rtc_eip or "srs-default",
                    )
                    return web.Response(
                        status=resp.status,
                        body=body,
                        headers={
                            "Content-Type": resp.headers.get("Content-Type", "application/sdp"),
                        },
                    )
        except aiohttp.ClientError as e:
            logger.error("WHEP proxy connect failed: %s", e)
            return web.Response(status=502, text=f"SRS WHEP unavailable: {e}")

    app.router.add_post("/srs-whep/{path:.*}", srs_whep_proxy)

    app.router.add_static('/', path='web')
