#!/usr/bin/env python3
import asyncio
import hashlib
import hmac
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from aiohttp import ClientSession, ClientTimeout, web


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("LIVETALKING_DATA_DIR", ROOT / "data")).resolve()
AVATARS_DIR = (DATA_DIR / "avatars").resolve()
WORK_DIR = Path(os.getenv("RUNTIME_WORK_DIR", ROOT / "runtime-manager" / "work")).resolve()
STATE_FILE = Path(os.getenv("RUNTIME_STATE_FILE", ROOT / "runtime-manager" / "state.json")).resolve()
TOKEN = os.getenv("RUNTIME_MANAGER_TOKEN", "")
AUTOSTART = os.getenv("LIVETALKING_AUTOSTART", "0") == "1"
HEALTH_URL = os.getenv("LIVETALKING_HEALTH_URL", "http://127.0.0.1:8010/api/admin/config")
AUDIO_SESSION_URL = os.getenv("LIVETALKING_AUDIO_SESSION_URL", "http://127.0.0.1:8010/api/audio/session")
COMMAND_TEMPLATE = os.getenv(
    "LIVETALKING_COMMAND_TEMPLATE",
    ".venv/bin/python app.py --transport rtcpush --model {model} --avatar_id {avatar_id} "
    "--batch_size 4 --max_session 1 --push_url "
    "http://127.0.0.1:10100/rtc/v1/whip/?app=live&stream=livestream&eip=127.0.0.1",
)

WORK_DIR.mkdir(parents=True, exist_ok=True)
AVATARS_DIR.mkdir(parents=True, exist_ok=True)


class RuntimeSupervisor:
    def __init__(self):
        self.lock = threading.RLock()
        self.operation_lock = threading.Lock()
        self.child = None
        self.jobs = {}
        self.state = self._load_state()

    def _load_state(self):
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        return {"revision": 0, "model": "wav2lip", "avatar_id": "wav2lip256_avatar1"}

    def _save_state(self):
        temp = STATE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, indent=2))
        temp.replace(STATE_FILE)

    def start(self, state=None):
        if not AUTOSTART:
            return
        with self.lock:
            if state:
                self.state = dict(state)
            self.stop()
            # Let SRS release the previous WHIP publisher before a new one connects.
            time.sleep(float(os.getenv("LIVETALKING_RESTART_COOLDOWN", "2")))
            command = COMMAND_TEMPLATE.format(**self.state)
            self.child = subprocess.Popen(shlex.split(command), cwd=ROOT)

    def stop(self):
        with self.lock:
            if not self.child or self.child.poll() is not None:
                self.child = None
                return
            self.child.terminate()
            try:
                self.child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=10)
            self.child = None
            # Brief pause so SRS can drop the old livestream publisher.
            time.sleep(float(os.getenv("LIVETALKING_STOP_COOLDOWN", "1")))

    def process_avatar(self, task_id, payload):
        task = self.jobs[task_id]
        if not self.operation_lock.acquire(blocking=False):
            task.update(status="failed", error="another GPU operation is already running")
            return
        previous = dict(self.state)
        try:
            task.update(status="running", progress=5)
            source = WORK_DIR / f"{task_id}.video"
            if payload.get("source_url"):
                self._download(payload["source_url"], source)
            else:
                source = Path(payload["source_path"]).resolve()
                if not source.is_file():
                    raise ValueError("source_path does not exist")

            self.stop()
            task["progress"] = 15
            avatar_id = self._safe_identifier(payload["avatar_id"])
            model = payload.get("model", "wav2lip")
            if model != "wav2lip":
                raise ValueError("Only wav2lip is enabled in v1")
            params = payload.get("parameters") or {}
            raw_pads = params.get("pads", "0 10 0 0")
            if isinstance(raw_pads, (list, tuple)):
                pads = [str(part) for part in raw_pads]
            else:
                pads = str(raw_pads).split()
            command = [
                os.getenv("LIVETALKING_PYTHON", str(ROOT / ".venv" / "bin" / "python")),
                "-m", "avatars.wav2lip.genavatar",
                "--video_path", str(source), "--avatar_id", avatar_id,
                "--save_path", str(AVATARS_DIR),
                "--img_size", str(max(256, int(params.get("img_size", 256)))),
                "--face_det_batch_size", str(int(params.get("face_det_batch_size", 16))),
                "--teeth_suppression", str(int(params.get("teeth_suppression", 25))),
                "--pads", *pads,
            ]
            if bool(params.get("nosmooth", False)):
                command.append("--nosmooth")
            subprocess.run(command, cwd=ROOT, check=True)
            task["progress"] = 85

            artifact = Path(payload.get("artifact_destination") or (WORK_DIR / f"{avatar_id}.tar.gz")).resolve()
            artifact.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(artifact, "w:gz") as archive:
                archive.add(AVATARS_DIR / avatar_id, arcname=avatar_id)
            checksum = self._sha256(artifact)
            if payload.get("artifact_upload_url"):
                headers = self._normalize_headers(payload.get("artifact_upload_headers") or {})
                request = Request(
                    payload["artifact_upload_url"],
                    data=artifact.read_bytes(),
                    headers=headers,
                    method="PUT",
                )
                with urlopen(request, timeout=300) as response:
                    if response.status >= 400:
                        raise RuntimeError(f"artifact upload failed: HTTP {response.status}")

            task.update(
                status="completed", progress=100,
                artifact_path=payload.get("artifact_path", str(artifact)),
                artifact_checksum=checksum,
            )
        except Exception as error:
            task.update(status="failed", error=str(error))
        finally:
            self.start(previous)
            self.operation_lock.release()

    async def deploy(self, payload):
        revision = int(payload["revision"])
        avatar_id = self._safe_identifier(payload["avatar_id"])
        model = payload.get("model", "wav2lip")
        if not self.operation_lock.acquire(blocking=False):
            return {"status": "failed", "error": "another GPU operation is already running"}
        previous = dict(self.state)
        artifact = WORK_DIR / f"deploy-{revision}.tar.gz"
        backup = None
        try:
            if payload.get("artifact_url"):
                await asyncio.to_thread(self._download, payload["artifact_url"], artifact)
            else:
                source = Path(payload["artifact_path"]).resolve()
                if not source.is_file():
                    raise ValueError("artifact_path does not exist")
                shutil.copy2(source, artifact)
            expected = payload.get("artifact_checksum")
            if expected and not hmac.compare_digest(self._sha256(artifact), expected):
                raise ValueError("artifact checksum mismatch")

            self.stop()
            staging = Path(tempfile.mkdtemp(prefix="avatar-", dir=WORK_DIR))
            self._safe_extract(artifact, staging)
            extracted = staging / avatar_id
            if not extracted.is_dir():
                raise ValueError("artifact does not contain the requested avatar")
            target = AVATARS_DIR / avatar_id
            if target.exists():
                backup = WORK_DIR / f"backup-{avatar_id}-{revision}"
                if backup.exists():
                    shutil.rmtree(backup)
                target.replace(backup)
            extracted.replace(target)
            shutil.rmtree(staging, ignore_errors=True)

            next_state = {"revision": revision, "model": model, "avatar_id": avatar_id}
            self.start(next_state)
            health = await self.wait_for_health()
            self.state = next_state
            self._save_state()
            if backup:
                shutil.rmtree(backup, ignore_errors=True)
            return {"status": "healthy", "health": health}
        except Exception as error:
            target = AVATARS_DIR / avatar_id
            if backup and backup.exists():
                shutil.rmtree(target, ignore_errors=True)
                backup.replace(target)
            self.start(previous)
            return {"status": "failed", "error": str(error), "rolled_back_to": previous}
        finally:
            self.operation_lock.release()

    async def wait_for_health(self):
        if not AUTOSTART:
            return {"managed": False, "state": self.state, "note": "LIVETALKING_AUTOSTART=0"}
        deadline = time.monotonic() + 90
        async with ClientSession(timeout=ClientTimeout(total=5)) as session:
            while time.monotonic() < deadline:
                if self.child and self.child.poll() is not None:
                    raise RuntimeError(f"LiveTalking exited with code {self.child.returncode}")
                try:
                    async with session.get(HEALTH_URL) as response:
                        if response.status == 200:
                            return {"managed": True, "http_status": 200, "state": self.state}
                except Exception:
                    pass
                await asyncio.sleep(2)
        raise TimeoutError("LiveTalking health check timed out")

    @staticmethod
    def _download(url, destination):
        request = Request(url, headers={"User-Agent": "LiveTalking-Runtime-Manager/1.0"})
        with urlopen(request, timeout=300) as response, open(destination, "wb") as output:
            shutil.copyfileobj(response, output)

    @staticmethod
    def _normalize_headers(headers):
        """Coerce Laravel/S3 temporaryUploadUrl headers into str->str for urllib."""
        if not headers:
            return {}
        if not isinstance(headers, dict):
            raise TypeError(f"artifact_upload_headers must be a dict, got {type(headers).__name__}")
        normalized = {}
        for key, value in headers.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                if not value:
                    continue
                value = value[0]
            normalized[str(key)] = value if isinstance(value, (str, bytes)) else str(value)
        return normalized

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with open(path, "rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _safe_identifier(value):
        value = str(value)
        if not value or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in value):
            raise ValueError("invalid avatar_id")
        return value

    @staticmethod
    def _safe_extract(archive_path, destination):
        destination = destination.resolve()
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                target = (destination / member.name).resolve()
                if destination not in target.parents and target != destination:
                    raise ValueError("unsafe archive path")
                if member.issym() or member.islnk():
                    raise ValueError("archive links are not allowed")
            archive.extractall(destination)


supervisor = RuntimeSupervisor()


@web.middleware
async def authenticate(request, handler):
    if request.path == "/up":
        return await handler(request)
    supplied = request.headers.get("Authorization", "")
    supplied = supplied[7:].strip() if supplied.lower().startswith("bearer ") else ""
    if not TOKEN:
        return web.json_response({"error": "RUNTIME_MANAGER_TOKEN is not configured"}, status=503)
    if not supplied or not hmac.compare_digest(supplied, TOKEN):
        return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


async def up(_request):
    return web.json_response({"ok": True})


async def health(_request):
    runtime_health = await supervisor.wait_for_health()
    return web.json_response({**runtime_health, "state": supervisor.state})


async def create_avatar_job(request):
    payload = await request.json()
    for required in ("avatar_id", "model"):
        if not payload.get(required):
            raise web.HTTPBadRequest(text=json.dumps({"error": f"{required} is required"}), content_type="application/json")
    if not payload.get("source_url") and not payload.get("source_path"):
        raise web.HTTPBadRequest(text=json.dumps({"error": "source_url or source_path is required"}), content_type="application/json")
    task_id = str(uuid.uuid4())
    supervisor.jobs[task_id] = {"task_id": task_id, "status": "pending", "progress": 0, "error": None}
    threading.Thread(target=supervisor.process_avatar, args=(task_id, payload), daemon=True).start()
    return web.json_response(supervisor.jobs[task_id], status=202)


async def avatar_job(request):
    task = supervisor.jobs.get(request.match_info["task_id"])
    if not task:
        raise web.HTTPNotFound(text=json.dumps({"error": "task not found"}), content_type="application/json")
    return web.json_response(task)


async def deploy(request):
    return web.json_response(await supervisor.deploy(await request.json()))


async def audio_session(_request):
    timeout = ClientTimeout(total=10)
    async with ClientSession(timeout=timeout) as session:
        async with session.post(
            AUDIO_SESSION_URL,
            json={"sessionid": "0"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as response:
            body = await response.json(content_type=None)
            return web.json_response(body, status=response.status)


def create_app():
    app = web.Application(middlewares=[authenticate], client_max_size=1024 ** 2)
    app.router.add_get("/up", up)
    app.router.add_get("/internal/health", health)
    app.router.add_post("/internal/avatar-jobs", create_avatar_job)
    app.router.add_get("/internal/avatar-jobs/{task_id}", avatar_job)
    app.router.add_post("/internal/deployments", deploy)
    app.router.add_post("/internal/audio-sessions", audio_session)
    return app


if __name__ == "__main__":
    if AUTOSTART:
        supervisor.start()
    web.run_app(create_app(), host=os.getenv("RUNTIME_MANAGER_HOST", "127.0.0.1"), port=int(os.getenv("RUNTIME_MANAGER_PORT", "8090")))
