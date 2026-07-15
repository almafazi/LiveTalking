#!/bin/bash
# 在 vast.ai 之类的主机上启动 SRS + LiveTalking（rtcpush 模式）。
#
# ⚠️ 以下路径依赖具体容器环境，请按你的主机调整：
#   - SRS_DIR：SRS 安装目录
#   - PY / LIVETALKING_PYTHON：LiveTalking 的 Python 解释器
#   - LT_DIR：本脚本会自动取仓库根目录（脚本上一级）
#
# 播放方式二选一（打开对应网页）：
#   1) FLV（默认，最稳）      -> /rtcpushapi.html
#   2) WHEP 低延迟 WebRTC/TCP -> /rtcpushwhep.html
#      需额外：把 SRS 的 RTC TCP 端口 10200 映射到公网，并设置
#      export SRS_RTC_EIP="<公网IP>:<10200对应的外部端口>"
#      （LiveTalking 进程读取该变量，注入到 WHEP answer 的候选地址）
set -e

# Vast maps container TCP 10200 to a dynamic public port. Advertise that public
# address in the WHEP SDP answer unless the operator supplied an explicit value.
if [ -z "${SRS_RTC_EIP:-}" ] && [ -n "${PUBLIC_IPADDR:-}" ] && [ -n "${VAST_TCP_PORT_10200:-}" ]; then
  export SRS_RTC_EIP="${PUBLIC_IPADDR}:${VAST_TCP_PORT_10200}"
fi

export CANDIDATE="${PUBLIC_IPADDR:-${CANDIDATE:-}}"
SRS_DIR="${SRS_DIR:-/usr/local/srs}"
LT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${LIVETALKING_PYTHON:-/venv/livetalking/bin/python}"
if [ ! -x "$PY" ]; then PY=python3; fi

# 1) 启动 SRS（若 API 未响应）
if ! curl -sf http://127.0.0.1:10100/api/v1/versions >/dev/null 2>&1; then
  echo "Starting SRS..."
  pkill -f "${SRS_DIR}/objs/srs" 2>/dev/null || true
  sleep 1
  rm -f "${SRS_DIR}/objs/srs.pid"
  (cd "$SRS_DIR" && nohup ./objs/srs -c conf/livetalking.conf >>/var/log/srs.log 2>&1 &)
  sleep 2
fi

# 2) 启动 LiveTalking（rtcpush，WHIP 推流到本机 SRS）
if ! curl -sf http://127.0.0.1:8010/rtcpushapi.html >/dev/null 2>&1; then
  echo "Starting LiveTalking..."
  pkill -f 'app.py --transport' 2>/dev/null || true
  sleep 1
  (cd "$LT_DIR" && nohup "$PY" app.py \
    --transport rtcpush \
    --model wav2lip \
    --avatar_id wav2lip256_avatar1 \
    --listenport 8010 \
    --batch_size 4 \
    --push_url 'http://127.0.0.1:10100/rtc/v1/whip/?app=live&stream=livestream&eip=127.0.0.1:10001' \
    --max_session 1 \
    >>/var/log/livetalking.log 2>&1 &)
  sleep 5
fi

echo "SRS:  $(curl -s http://127.0.0.1:10100/api/v1/versions | head -c 120)"
echo "UI:   http://${PUBLIC_IPADDR:-HOST}:${VAST_TCP_PORT_8010:-PORT}/rtcpushapi.html"
echo "WHEP: ${SRS_RTC_EIP:-not configured} (open /rtcpushwhep.html)"
curl -s http://127.0.0.1:10100/api/v1/streams/ | head -c 400; echo
