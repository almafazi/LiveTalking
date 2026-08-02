# LiveTalking — Workflow (Vast.ai + rtcpush + SRS)

> Runbook ini berisi detail troubleshooting GPU, model, dan transport pada
> instance Vast. Untuk deployment production lengkap yang memasangkan Vast.ai
> dengan Laravel di aaPanel, gunakan
> [`docs/deployment-aapanel-vast.md`](docs/deployment-aapanel-vast.md).

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

### WHEP/WebRTC-over-TCP untuk player utama

Expose container **TCP 10200** di Vast.ai, lalu set candidate publik pada proses
LiveTalking (bukan pada browser):

```bash
export SRS_RTC_EIP="$PUBLIC_IPADDR:$VAST_TCP_PORT_10200"
```

Domain HTTPS tetap meneruskan UI/API dan `/srs-whep/*` ke LiveTalking `8010`.
Media WebRTC TCP `10200` harus dapat dicapai langsung oleh browser; jangan
dilewatkan melalui reverse proxy HTTP biasa. Halaman `elevenlabs.html` mencoba
WHEP terlebih dahulu dan otomatis kembali ke HTTP-FLV bila signaling, ICE, atau
media tidak siap dalam 8 detik.

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

### Normalisasi video sumber avatar

Sebelum menjalankan **Process avatar** dari control-plane, normalisasikan video
sumber agar sesuai dengan pipeline LiveTalking. Untuk satu session Wav2Lip atau
Easy-Wav2Lip, titik awal yang direkomendasikan adalah:

```text
Resolusi       720x1280 (portrait)
Frame rate     CFR 25 FPS
Pixel format   yuv420p
Video codec    H.264
Audio          tidak diperlukan
```

Contoh konversi:

```bash
ffmpeg -y -i input.mp4 \
  -vf "fps=25,scale=720:1280:flags=lanczos" \
  -an \
  -c:v libx264 \
  -preset fast \
  -crf 18 \
  -pix_fmt yuv420p \
  -movflags +faststart \
  avatar-optimized.mp4
```

Untuk sumber yang bukan rasio portrait 9:16, pertahankan aspect ratio dan beri
padding agar gambar tidak terdistorsi:

```bash
ffmpeg -y -i input.mp4 \
  -vf "fps=25,scale=720:1280:force_original_aspect_ratio=decrease:flags=lanczos,pad=720:1280:(ow-iw)/2:(oh-ih)/2" \
  -an \
  -c:v libx264 -preset fast -crf 18 \
  -pix_fmt yuv420p -movflags +faststart \
  avatar-optimized.mp4
```

Validasi file sebelum di-upload ke admin. Jangan hanya mengandalkan metadata;
pastikan seluruh frame dapat didekode:

```bash
ffprobe -v error \
  -show_entries stream=width,height,avg_frame_rate,nb_frames \
  -show_entries format=duration,size \
  -of json avatar-optimized.mp4

ffmpeg -v error -i avatar-optimized.mp4 -map 0:v:0 -f null -
```

Menurunkan 1080x1920 menjadi 720x1280 mengurangi jumlah piksel sekitar 56%.
Ini meringankan copy frame, compositing wajah, encoding H.264, bandwidth FLV,
dan decoding browser. CFR 25 juga menyamakan frame rate sumber dengan default
LiveTalking sehingga pacing lebih konsisten.

Normalisasi tidak dapat menciptakan gerakan baru. Jika video sumber sering diam
atau hanya memiliki perubahan yang sangat kecil, avatar tetap dapat terlihat
pause walaupun server mengirim 25 FPS. Gunakan video 15-30 detik dengan gerakan
kepala/badan kecil tetapi berkelanjutan, tanpa berhenti lama pada satu pose, dan
usahakan pose awal serta akhir serupa agar loop maju-mundur terlihat mulus.

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

## Troubleshooting: HTTP-FLV macet lewat control-plane / Cloudflare

Jika audio tetap berjalan tetapi gambar avatar tiba-tiba berhenti pada halaman
control-plane, periksa console browser. Gejala berikut menunjukkan koneksi
HTTP-FLV diputus di jalur HTTP/3/QUIC, bukan masalah inference GPU:

```text
ERR_QUIC_PROTOCOL_ERROR.QUIC_PACKET_WRITE_ERROR
Fetch stream meet Early-EOF
UnrecoverableEarlyEof
```

Alur media pada deployment control-plane adalah:

```text
Browser
  -> https://DOMAIN/media/srs-live/live/livestream.flv
  -> Cloudflare / reverse proxy aaPanel
  -> http://VAST_IP:VAST_TCP_PORT_8010/srs-live/live/livestream.flv
  -> LiveTalking -> SRS
```

Walaupun reverse proxy meneruskan request ke Vast dengan HTTP/1.1, browser dapat
terhubung ke Cloudflare menggunakan HTTP/3. Kegagalan QUIC pada koneksi FLV yang
berumur panjang membuat player berhenti pada frame terakhir sementara request
audio ElevenLabs tetap berjalan secara terpisah.

### Verifikasi protokol browser

Di Chrome/Edge buka **DevTools -> Network**, aktifkan kolom **Protocol**, lalu
cari request `livestream.flv`:

- `h3`: browser memakai HTTP/3/QUIC.
- `h2`: browser memakai HTTP/2.
- `http/1.1`: browser memakai HTTP/1.1.

Cek apakah domain mengiklankan HTTP/3:

```bash
curl -sI https://DOMAIN/ | grep -i alt-svc
```

Header seperti berikut berarti HTTP/3 ditawarkan kepada browser:

```text
alt-svc: h3=":443"; ma=86400
```

### Mitigasi Cloudflare

Matikan **Network -> HTTP/3 (with QUIC)** untuk domain control-plane. Setelah
itu tutup browser sepenuhnya atau gunakan Incognito, lakukan hard refresh, dan
pastikan request `livestream.flv` berubah menjadi `h2`. Browser dapat menyimpan
informasi `Alt-Svc` dari koneksi sebelumnya sehingga refresh biasa belum tentu
langsung menghentikan penggunaan `h3`.

### Konfigurasi reverse proxy media

Pastikan endpoint `/media/` tidak memakai buffering atau cache proxy:

```nginx
location /media/ {
    proxy_pass http://VAST_RUNTIME/;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_cache off;
    proxy_request_buffering off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    gzip off;
    add_header X-Accel-Buffering no;
}
```

Untuk ketahanan tambahan, player mpegts.js di control-plane sebaiknya memakai
`enableStashBuffer: false`, `liveBufferLatencyChasing: true`, dan menyambungkan
ulang player ketika menerima error `EarlyEof`.

### Audio avatar ElevenLabs tersendat antar-chunk

Jangan mengirim output conversational ElevenLabs sebagai rangkaian file WAV
pendek ke `/media/api/elevenlabs/audio`. `put_audio_file()` memberi event
`start` dan `end` pada setiap file. Jika jeda antar-request melewati timeout
antrean ASR 10 ms, runtime menyisipkan frame silence dan log akan berganti cepat:

```text
状态切换：说话 → 静音
状态切换：静音 → 说话
```

Jalur utama control-plane menggunakan satu WebSocket PCM kontinu:

```text
Browser /media/api/elevenlabs/stream
  -> reverse proxy WebSocket
  -> runtime-manager /api/elevenlabs/stream
  -> frame PCM16 mono 16 kHz per 20 ms
```

Di ElevenLabs, set agent output ke `pcm_16000`. Reverse proxy harus meneruskan
header Upgrade dan tidak melakukan response buffering:

```nginx
location = /media/api/elevenlabs/stream {
    proxy_pass http://VAST_RUNTIME/api/elevenlabs/stream;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_buffering off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

Verifikasi di DevTools bahwa request `stream` mendapat status `101 Switching
Protocols`. Pada runtime, satu jawaban normal menghasilkan tepat satu pasang log
`PCM response start` dan `PCM response complete`. Endpoint WAV lama tetap ada
sebagai fallback, tetapi bukan jalur utama low-latency.

### Error audio ElevenLabs 409

Respons berikut bukan error FLV/QUIC:

```text
/media/api/elevenlabs/audio -> HTTP 409
```

Ini berarti chunk audio dari generation lama tiba setelah interrupt/barge-in.
Client sebaiknya membatalkan upload yang masih berjalan dan mengabaikan `409`
untuk chunk stale. Jangan memakai error ini sebagai indikator masalah GPU atau
stream video.

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
