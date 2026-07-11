# LiveTalking — Workflow (Vast.ai + rtcpush + SRS)

Runbook agar **musetalk / wav2lip** langsung jalan di instance ini (fork `almafazi/LiveTalking`).

## Ringkas: start ulang (sudah terpasang)

```bash
supervisorctl status srs livetalking
supervisorctl restart srs
sleep 3
supervisorctl restart livetalking
# tunggu model load ~1–2 menit (musetalk)
curl -s http://127.0.0.1:10100/api/v1/versions
curl -sI http://127.0.0.1:8010/rtcpushapi.html
curl -s http://127.0.0.1:10100/api/v1/streams/ | head -c 400; echo
```

**UI (tanpa auth):**  
`http://$PUBLIC_IPADDR:$VAST_TCP_PORT_8010/rtcpushapi.html`  
Contoh: `http://99.213.88.59:43117/rtcpushapi.html`

---

## Arsitektur

```
Browser ──TCP :8010──► LiveTalking (API + /rtcpushapi.html + /srs-live FLV proxy)
                              │
                              └──WHIP localhost──► SRS
                                   API      :10100
                                   RTC UDP  :10001   (publish LT→SRS)
                                   HTTP-FLV :8088    (proxy lewat /srs-live)
                                   RTC TCP  :10200   (opsional WHEP)
```

| Container port | Public (contoh) | Fungsi |
|----------------|-----------------|--------|
| TCP 8010 | `$VAST_TCP_PORT_8010` | UI + API LiveTalking |
| TCP 10100 | `$VAST_TCP_PORT_10100` | SRS API (internal OK) |
| TCP 10200 | `$VAST_TCP_PORT_10200` | WHEP media TCP (opsional) |
| UDP 10001 | `$VAST_UDP_PORT_10001` | RTC media (publish lokal) |

**Jangan** ikuti docs AutoDL 1:1 (Docker SRS, port 1985/8000) — di Vast port fixed, no Docker-in-Docker, UDP NAT beda.

---

## Path penting

| Path | Isi |
|------|-----|
| `/workspace/LiveTalking` | Kode (fork almafazi) |
| `/venv/main` | Python + torch cu128 |
| `/usr/local/srs` | Binary SRS 5.x |
| `/usr/local/srs/conf/livetalking.conf` | Config SRS (dari `deploy/srs-livetalking.conf`) |
| `/opt/supervisor-scripts/srs.sh` | Start SRS |
| `/opt/supervisor-scripts/livetalking.sh` | Start LiveTalking (**workflow utama**) |
| `/etc/supervisor/conf.d/{srs,livetalking}.conf` | Supervisor |
| `/var/log/portal/{srs,livetalking}.log` | Log |

---

## Model & avatar

### MuseTalk (default di script sekarang)

```
models/musetalkV15/unet.pth          # ~3.4GB
models/musetalkV15/musetalk.json
models/sd-vae -> sd-vae-ft-mse/      # symlink
models/sd-vae-ft-mse/{config.json,diffusion_pytorch_model.bin,...}
models/whisper/{config.json,pytorch_model.bin,preprocessor_config.json,tiny.pt}
data/avatars/musetalk_avatar1/       # latents.pt, coords.pkl, full_imgs/, mask/
```

**Unduh model (HF mirror):**
```bash
source /venv/main/bin/activate
cd /workspace/LiveTalking
python - <<'PY'
from huggingface_hub import hf_hub_download
import os, shutil
repo="yiliAST/livetalking-assets"
files=[
 "models/musetalkV15/musetalk.json","models/musetalkV15/unet.pth",
 "models/sd-vae-ft-mse/config.json","models/sd-vae-ft-mse/diffusion_pytorch_model.bin",
 "models/sd-vae-ft-mse/diffusion_pytorch_model.safetensors",
 "models/whisper/config.json","models/whisper/preprocessor_config.json",
 "models/whisper/pytorch_model.bin","models/whisper/tiny.pt",
]
for f in files:
    p=hf_hub_download(repo_id=repo, filename=f, local_dir="/tmp/lt-muse")
    dst=os.path.join(".", f); os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(p, dst); print("ok", dst)
if not os.path.exists("models/sd-vae"):
    os.symlink("sd-vae-ft-mse", "models/sd-vae")
PY
```

**Avatar:** pack resmi docs (Xunlei) sering tidak bisa di-auto. Generate dari video sunyi:
```bash
# butuh: face_recognition, face-parse weights (opsional dwpose)
ffmpeg -y -i sample.mp4 -t 5 -an /tmp/avatar_src.mp4
cd /workspace/LiveTalking
python -m avatars.musetalk.genavatar --avatar_id musetalk_avatar1 --file /tmp/avatar_src.mp4
```

### Wav2Lip

```
models/wav2lip.pth                   # dari wav2lip256.pth
data/avatars/wav2lip256_avatar1/
```

```bash
# Google Drive folder resmi LiveTalking
gdown --folder 'https://drive.google.com/drive/folders/1FOC_MD6wdogyyX_7V1d4NDIO7P9NlSAJ' -O /tmp/lt-models
cp /tmp/lt-models/wav2lip256.pth /workspace/LiveTalking/models/wav2lip.pth
tar -xzf /tmp/lt-models/wav2lip256_avatar1.tar.gz -C /workspace/LiveTalking/data/avatars/
```

---

## Ganti model (musetalk ↔ wav2lip)

Edit `/opt/supervisor-scripts/livetalking.sh`:

**MuseTalk:**
```bash
--model musetalk \
--avatar_id musetalk_avatar1 \
--batch_size 2 \
```

**Wav2Lip:**
```bash
--model wav2lip \
--avatar_id wav2lip256_avatar1 \
--batch_size 4 \
```

Lalu:
```bash
supervisorctl restart livetalking
# jika WHIP gagal / stream macet:
supervisorctl restart srs && sleep 3 && supervisorctl restart livetalking
```

`batch_size`: musetalk lebih berat (RTX 5060 Ti 16GB → 2 aman, coba 4 jika VRAM longgar). Target log: `final fps` ≥ 25.

---

## Wajib di workflow rtcpush (Vast)

### 1) Jangan apply ICE patch vast saat rtcpush

`app.py` memanggil `vast_ice_patch` yang mem-bind `VAST_UDP_PORT_*`. Itu untuk **webrtc P2P**, **bukan** WHIP ke SRS localhost.

Di `livetalking.sh` sudah ada:
```bash
for _k in $(env | awk -F= '/^VAST_UDP_PORT_/ {print $1}'); do unset "$_k"; done
unset PUBLIC_IPADDR VAST_PUBLIC_IP
```

Tanpa ini: sering `WHIP ok 201` tapi `publish.active=false` / FLV kosong.

### 2) Port LiveTalking = 8010

Bukan `18010` di script contoh repo — harus match open port instance.

### 3) push_url WHIP

```text
http://127.0.0.1:10100/rtc/v1/whip/?app=live&stream=livestream&eip=127.0.0.1:10001
```

### 4) Restart order

1. SRS dulu  
2. Tunggu API `10100`  
3. LiveTalking  
4. Cek stream `publish.active=true` + video codec

### 5) Supervisor startsecs

MuseTalk load lama → `startsecs=60` di conf (bukan 15).

---

## Install dari nol (instance baru)

```bash
# 1) Clone
git clone --depth 1 https://github.com/almafazi/LiveTalking.git /workspace/LiveTalking
source /venv/main/bin/activate
uv pip install -r /workspace/LiveTalking/requirements.txt gdown face_recognition "setuptools<81"

# 2) SRS binary (no Docker)
cd /tmp && curl -fL -o srs.zip \
  https://github.com/ossrs/srs/releases/download/v5.0-b0/SRS-CentOS7-x86_64-5.0-b0.zip
unzip -o srs.zip -d /tmp/srs-extract
cp -a /tmp/srs-extract/SRS-CentOS7-x86_64-5.0-b0/usr/local/srs /usr/local/
cp /workspace/LiveTalking/deploy/srs-livetalking.conf /usr/local/srs/conf/livetalking.conf
chmod +x /usr/local/srs/objs/srs

# 3) Models + avatar (lihat section di atas)

# 4) Supervisor scripts (salin dari instance ini atau buat ulang isi section Path penting)
chmod +x /opt/supervisor-scripts/srs.sh /opt/supervisor-scripts/livetalking.sh
supervisorctl reread && supervisorctl update
supervisorctl start srs livetalking
```

Isi minimal `livetalking.sh` — lihat file live di `/opt/supervisor-scripts/livetalking.sh` (sumber kebenaran).

---

## Verifikasi sehat

```bash
# SRS
curl -s http://127.0.0.1:10100/api/v1/versions

# Stream aktif + H264
curl -s http://127.0.0.1:10100/api/v1/streams/

# UI + FLV
curl -sI http://127.0.0.1:8010/rtcpushapi.html
curl -s --max-time 3 -o /tmp/t.flv http://127.0.0.1:8010/srs-live/live/livestream.flv
ls -la /tmp/t.flv   # harus > 100KB

# Drive teks
curl -s -X POST http://127.0.0.1:8010/human \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hello test","type":"echo","sessionid":"0"}'

# Log
tail -f /var/log/portal/livetalking.log
# cari: WHIP ok, Connection state is connected, final fps:~25
```

---

## Troubleshooting

| Gejala | Perbaikan |
|--------|-----------|
| `WHIP` 201 tapi stream kosong | Pastikan `VAST_UDP_*` di-unset; restart srs lalu livetalking |
| `Server disconnected` saat WHIP | Stream lama masih pegang SRS → `supervisorctl restart srs` dulu |
| `Address already in use :10001` di vast_ice | Normal jika patch aktif; untuk rtcpush matikan patch |
| Port 18010 / blank UI | Pakai **8010** + public `$VAST_TCP_PORT_8010` |
| FPS < 25 | Turunkan `batch_size`, cek `nvidia-smi` |
| Avatar missing | Cek `data/avatars/<id>/latents.pt` (musetalk) atau `coords.pkl`+imgs (wav2lip) |
| OOM GPU | `batch_size=1` atau tutup proses GPU lain |
| WHEP docs (`:1985` UDP) gagal | Diharapkan di Vast; pakai **FLV** `rtcpushapi.html` |
| Disk hilang setelah recycle | `workspace_is_volume=false` — sync model/kode keluar box |

---

## Catatan performa

- Inferensi (musetalk/wav2lip) **sama** path resmi; yang di-custom hanya transport deploy.
- FLV = lebih stabil di Vast, latency sedikit lebih tinggi dari WebRTC murni.
- EdgeTTS butuh outbound internet.
- GPU: RTX 5060 Ti + torch **cu128** (Blackwell butuh CUDA ≥ 12.8 wheels).

---

## Checklist next time (musetalk)

- [ ] `srs` RUNNING, API 10100 OK  
- [ ] Model files di `models/musetalkV15` + `sd-vae` + `whisper`  
- [ ] Avatar `data/avatars/musetalk_avatar1/latents.pt`  
- [ ] Script: `--model musetalk`, ICE vast **off**, port **8010**  
- [ ] Restart order: SRS → LT  
- [ ] `publish.active=true`, fps ≥ 25  
- [ ] Browser: `rtcpushapi.html` → Send teks