###############################################################################
#  WebRTC 连接管理 + RTC 音频/视频接收
###############################################################################

import json
import asyncio
import random
import copy
from typing import Dict, Optional
import queue

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, RTCConfiguration
from aiortc.rtcrtpsender import RTCRtpSender

from utils.logger import logger


# def _rand_session_id(n: int = 6) -> int:
#     """生成 N 位随机 session ID"""
#     return random.randint(10 ** (n - 1), 10 ** n - 1)


from server.session_manager import session_manager
from server.session_manager import MaxSessionError


def _prefer_h264(pc: RTCPeerConnection) -> None:
    """让所有 video transceiver 优先使用 H264（其次 VP8），便于 SRS remux / 浏览器兼容。"""
    capabilities = RTCRtpSender.getCapabilities("video")
    preferences = (
        [c for c in capabilities.codecs if c.name == "H264"]
        + [c for c in capabilities.codecs if c.name == "VP8"]
        + [c for c in capabilities.codecs if c.name == "rtx"]
    )
    if not preferences:
        return
    for transceiver in pc.getTransceivers():
        sender = getattr(transceiver, "sender", None)
        track = getattr(sender, "track", None)
        if track is not None and track.kind == "video":
            transceiver.setCodecPreferences(preferences)


class RTCManager:
    """
    WebRTC 连接管理器。
    
    管理 PeerConnection 生命周期、音视频轨道收发、DataChannel。
    """

    def __init__(self, opt):
        """
        Args:
            opt: 全局配置
        """
        self.opt = opt
        self.pcs: set = set()

    async def handle_offer(self, request):
        """处理 WebRTC offer 信令"""
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        # 通过 SessionManager 构建（内部会检查 max_session）
        try:
            sessionid = await session_manager.create_session(params)
        except MaxSessionError as e:
            logger.warning("Rejecting offer: %s", e)
            return web.Response(
                content_type="application/json",
                text=json.dumps({"code": -1, "msg": str(e)}),
            )
        logger.info('offer sessionid=%s', sessionid)
        avatar_session = session_manager.get_session(sessionid)

        # 创建 PeerConnection
        ice_server = RTCIceServer(urls=self.opt.stun) #'stun:stun.freeswitch.org:3478'
        pc = RTCPeerConnection(
            configuration=RTCConfiguration(iceServers=[ice_server])
        )
        self.pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info("Connection state is %s", pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                await pc.close()
                self.pcs.discard(pc)
                session_manager.remove_session(sessionid)

        # 添加发送轨道
        from server.webrtc import HumanPlayer
        player = HumanPlayer(avatar_session)
        pc.addTrack(player.audio)
        pc.addTrack(player.video)

        # 设置编解码器偏好（H264 优先）
        _prefer_h264(pc)

        await pc.setRemoteDescription(offer)

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return web.Response(
            content_type="application/json",
            text=json.dumps({
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
                "sessionid": sessionid,
            }),
        )

    async def handle_rtcpush(self, push_url, sessionid: str):
        """RTCPush 模式：主动推流"""
        import aiohttp
        await session_manager.create_session({}, sessionid)
        avatar_session = session_manager.get_session(sessionid)

        pc = RTCPeerConnection()
        self.pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info("Connection state is %s", pc.connectionState)
            if pc.connectionState == "failed":
                await pc.close()
                self.pcs.discard(pc)

        from server.webrtc import HumanPlayer
        player = HumanPlayer(avatar_session)
        pc.addTrack(player.audio)
        pc.addTrack(player.video)

        # H264 优先，便于 SRS remux 成 FLV 给浏览器播放
        _prefer_h264(pc)

        await pc.setLocalDescription(await pc.createOffer())
        logger.info("rtcpush WHIP -> %s", push_url)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                push_url,
                data=pc.localDescription.sdp,
                headers={"Content-Type": "application/sdp"},
            ) as response:
                answer_sdp = await response.text()
                if response.status >= 400:
                    logger.error(
                        "WHIP failed status=%s body=%s",
                        response.status, answer_sdp[:500],
                    )
                    await pc.close()
                    self.pcs.discard(pc)
                    return
                logger.info(
                    "WHIP ok status=%s answer_len=%d",
                    response.status, len(answer_sdp),
                )

        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer_sdp, type='answer')
        )

    async def shutdown(self):
        """关闭所有 PeerConnection"""
        coros = [pc.close() for pc in self.pcs]
        await asyncio.gather(*coros)
        self.pcs.clear()
