# Deployment production: aaPanel + Vast.ai

Panduan ini menggunakan arsitektur dua mesin:

```text
Browser
  |
  | HTTPS domain
  v
aaPanel VPS
  |- Nginx/SSL -> 127.0.0.1:8080
  |- Laravel + Filament + React
  |- queue + scheduler
  `- PostgreSQL
       |
       | Runtime Manager API + media proxy
       v
Vast.ai GPU
  |- Runtime Manager :8090
  |- LiveTalking     :8010
  `- SRS RTC TCP    :10200
```

WHEP/WebRTC-over-TCP menjadi jalur video utama. HTTP-FLV melalui port `8010`
tetap tersedia sebagai fallback jika jaringan browser tidak dapat mencapai
RTC TCP Vast.

## Nilai yang perlu disiapkan

Catat nilai berikut agar tidak tertukar:

| Nama | Contoh | Keterangan |
|---|---|---|
| `APP_DOMAIN` | `aurora.example.com` | Domain yang diarahkan ke VPS aaPanel |
| `RUNTIME_TOKEN` | random minimal 32 byte | Harus sama di aaPanel dan Vast |
| `VAST_IP` | `203.0.113.20` | Nilai `PUBLIC_IPADDR` dari Vast |
| `VAST_PORT_8010` | `43117` | Port publik menuju LiveTalking |
| `VAST_PORT_8090` | `43118` | Port publik menuju Runtime Manager |
| `VAST_PORT_10200` | `43119` | Port publik RTC TCP untuk WHEP |

Generate token dan password berbeda untuk setiap fungsi:

```bash
openssl rand -hex 32
```

Jangan memasukkan API key, token, atau password ke Git.

## Bagian A: menyiapkan GPU Vast.ai

### 1. Buat instance

Gunakan image PyTorch dengan Python 3.12 dan CUDA yang sesuai GPU. Untuk GPU
Blackwell seperti RTX 50xx, gunakan build CUDA 12.8 atau lebih baru. Pilih mode
SSH dan disk yang cukup untuk model, avatar, serta cache; `50 GB` adalah titik
awal yang lebih aman daripada default 10 GB.

Tambahkan Docker create options berikut pada template Vast:

```text
-p 8010:8010 -p 8090:8090 -p 10200:10200
```

Vast biasanya memberikan port eksternal acak. Setelah instance aktif, lihat
**IP Port Info** atau jalankan:

```bash
echo "$PUBLIC_IPADDR"
echo "$VAST_TCP_PORT_8010"
echo "$VAST_TCP_PORT_8090"
echo "$VAST_TCP_PORT_10200"
```

Port `10100`, `10001`, dan `8088` hanya dipakai antarproses di dalam instance,
sehingga tidak perlu dibuka ke publik.

### 2. Clone dan install dependency

```bash
git clone https://github.com/almafazi/LiveTalking.git /workspace/LiveTalking
cd /workspace/LiveTalking

source /venv/main/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r runtime-manager/requirements.txt
```

Pastikan checkpoint dan avatar tersedia:

```bash
test -f models/wav2lip.pth
test -d data/avatars/wav2lip256_avatar1
```

Cara mengunduh/generate model dan avatar dijelaskan lebih rinci di
[`../WORKFLOW.md`](../WORKFLOW.md#model--avatar).

### 3. Install SRS

Jika image belum memiliki SRS, gunakan binary yang sudah dipakai oleh runbook
project ini:

```bash
cd /tmp
curl -fL -o srs.zip \
  https://github.com/ossrs/srs/releases/download/v5.0-b0/SRS-CentOS7-x86_64-5.0-b0.zip
unzip -o srs.zip -d /tmp/srs-extract
cp -a /tmp/srs-extract/SRS-CentOS7-x86_64-5.0-b0/usr/local/srs /usr/local/

cp /workspace/LiveTalking/deploy/srs-livetalking.conf \
  /usr/local/srs/conf/livetalking.conf
chmod +x /usr/local/srs/objs/srs
```

### 4. Konfigurasi Runtime Manager

```bash
cd /workspace/LiveTalking
cp deploy/vast/.env.runtime.example .env.runtime
chmod 600 .env.runtime
```

Edit `.env.runtime` dan isi `RUNTIME_MANAGER_TOKEN`. Jika environment Vast
tersedia pada proses Supervisor, script akan membentuk `SRS_RTC_EIP` otomatis
dari `PUBLIC_IPADDR` dan `VAST_TCP_PORT_10200`. Agar tidak bergantung pada itu,
isi eksplisit setelah melihat IP Port Info:

```dotenv
SRS_RTC_EIP=203.0.113.20:43119
```

Alamat tersebut harus menunjuk ke port publik yang dipetakan ke internal
`10200/tcp`, bukan port publik `8010`.

### 5. Jalankan permanen dengan Supervisor

```bash
mkdir -p /var/log/portal
chmod +x deploy/vast/srs.sh deploy/vast/runtime-manager.sh
cp deploy/vast/supervisor.conf.example \
  /etc/supervisor/conf.d/livetalking-stack.conf

supervisorctl reread
supervisorctl update
supervisorctl restart srs runtime-manager
```

Runtime Manager dijalankan dengan `LIVETALKING_AUTOSTART=1`, sehingga ia akan
menjalankan LiveTalking sebagai child process dan dapat melakukan restart serta
rollback ketika admin menekan **Publish**.

Verifikasi dari dalam Vast:

```bash
curl http://127.0.0.1:10100/api/v1/versions
curl http://127.0.0.1:8010/api/admin/config
curl http://127.0.0.1:8090/up

curl \
  -H 'Authorization: Bearer RUNTIME_TOKEN_ANDA' \
  http://127.0.0.1:8090/internal/health

curl http://127.0.0.1:10100/api/v1/streams/
```

Target akhirnya adalah stream `live/livestream` berstatus aktif dan health
LiveTalking mengembalikan HTTP `200`.

## Bagian B: menyiapkan control plane di aaPanel

Deployment yang disarankan adalah Docker Compose dari repository. aaPanel tetap
menangani domain, reverse proxy, SSL, firewall, dan pemantauan container.

### 1. Persiapan VPS

Di aaPanel:

1. Install **Nginx** dan **Docker Manager** dari App Store.
2. Arahkan DNS `A` untuk `APP_DOMAIN` ke IP VPS.
3. Buka firewall publik hanya untuk `80/tcp`, `443/tcp`, dan port panel/SSH
   yang memang digunakan.

Clone project melalui Terminal aaPanel:

```bash
mkdir -p /www/wwwroot
git clone https://github.com/almafazi/LiveTalking.git /www/wwwroot/aurora
cd /www/wwwroot/aurora

cp control-plane/.env.example control-plane/.env
chmod 600 control-plane/.env
```

### 2. Isi environment production

Edit `/www/wwwroot/aurora/control-plane/.env`. Contoh nilai penting:

```dotenv
APP_NAME=Aurora
APP_ENV=production
APP_DEBUG=false
APP_URL=https://aurora.example.com
APP_KEY=base64:HASIL_RANDOM_ANDA

DB_CONNECTION=pgsql
DB_HOST=postgres
DB_PORT=5432
DB_DATABASE=livetalking
DB_USERNAME=livetalking
DB_PASSWORD=PASSWORD_DATABASE_YANG_KUAT

SESSION_DRIVER=database
QUEUE_CONNECTION=database
CACHE_STORE=database

ELEVENLABS_API_KEY=API_KEY_ANDA
ELEVENLABS_AGENT_ID=AGENT_ID_ANDA

RUNTIME_MANAGER_URL=http://203.0.113.20:43118
RUNTIME_MANAGER_TOKEN=TOKEN_YANG_SAMA_DENGAN_VAST
RUNTIME_MANAGER_TIMEOUT=1200
RUNTIME_PUBLIC_URL=http://203.0.113.20:43117

AVATAR_DISK=s3
FILESYSTEM_DISK=s3
AWS_ACCESS_KEY_ID=ACCESS_KEY_ANDA
AWS_SECRET_ACCESS_KEY=SECRET_KEY_ANDA
AWS_DEFAULT_REGION=REGION_ANDA
AWS_BUCKET=BUCKET_ANDA
AWS_ENDPOINT=https://ENDPOINT-S3-ANDA
AWS_URL=https://PUBLIC-CDN-ATAU-BUCKET-ANDA
AWS_USE_PATH_STYLE_ENDPOINT=false

ADMIN_NAME=Administrator
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=PASSWORD_ADMIN_YANG_KUAT
```

Buat `APP_KEY` dengan:

```bash
printf 'base64:%s\n' "$(openssl rand -base64 32)"
```

`AVATAR_DISK=s3` wajib untuk arsitektur dua mesin: Laravel membuat URL upload
sementara agar Vast dapat mengunduh video sumber dan mengunggah artifact avatar.
Jika logo menggunakan disk yang sama, object logo atau `AWS_URL` harus dapat
dibaca publik agar muncul di frontend.

### 3. Build dan jalankan Compose

```bash
cd /www/wwwroot/aurora

docker compose \
  --env-file control-plane/.env \
  -f infra/docker-compose.control.yml \
  up -d --build

docker compose \
  --env-file control-plane/.env \
  -f infra/docker-compose.control.yml \
  exec app php artisan migrate --force

docker compose \
  --env-file control-plane/.env \
  -f infra/docker-compose.control.yml \
  exec app php artisan db:seed --force
```

Pastikan semua container hidup:

```bash
docker compose \
  --env-file control-plane/.env \
  -f infra/docker-compose.control.yml \
  ps

curl http://127.0.0.1:8080/api/public/config
```

Compose hanya bind ke `127.0.0.1:8080`; port tersebut sengaja tidak dibuka
langsung ke internet.

### 4. Domain, reverse proxy, dan SSL aaPanel

Di aaPanel buka **Website > Proxy Project** lalu:

1. Tambahkan domain `APP_DOMAIN`.
2. Proxy seluruh path `/` ke `http://127.0.0.1:8080`.
3. Matikan cache proxy.
4. Aktifkan WebSocket.
5. Pasang sertifikat Let's Encrypt dan aktifkan HTTPS.

Tambahkan parameter berikut pada konfigurasi Nginx website/proxy jika belum
ada, terutama untuk upload video avatar dan koneksi panjang:

```nginx
client_max_body_size 220m;
proxy_connect_timeout 60s;
proxy_send_timeout 3600s;
proxy_read_timeout 3600s;
proxy_buffering off;
```

Jika menggunakan Cloudflare, gunakan mode SSL **Full (strict)**. Jangan expose
port `8080` VPS melalui firewall karena aaPanel Nginx mengaksesnya lewat
loopback.

### 5. Verifikasi end-to-end

```bash
curl https://aurora.example.com/api/public/config
curl http://203.0.113.20:43117/api/admin/config
curl http://203.0.113.20:43118/up
```

Lalu cek melalui browser:

1. Buka `https://aurora.example.com/` dan pastikan avatar tampil.
2. Login ke `https://aurora.example.com/admin`.
3. Coba percakapan mic dan input teks.
4. Upload avatar kecil, jalankan **Process avatar**, lalu pantau queue.
5. Jalankan **Publish** dan pastikan Deployment berstatus `healthy`.

Jika UI tampil tetapi avatar kosong, cek urutannya:

```bash
# aaPanel
docker compose --env-file control-plane/.env \
  -f infra/docker-compose.control.yml logs --tail=100 nginx app queue

# Vast
supervisorctl status srs runtime-manager
tail -n 100 /var/log/portal/runtime-manager.log
tail -n 100 /var/log/portal/srs.log
curl http://127.0.0.1:10100/api/v1/streams/
```

## Update aplikasi

Di aaPanel:

```bash
cd /www/wwwroot/aurora
git pull --ff-only

docker compose --env-file control-plane/.env \
  -f infra/docker-compose.control.yml up -d --build

docker compose --env-file control-plane/.env \
  -f infra/docker-compose.control.yml exec app php artisan migrate --force
```

Di Vast:

```bash
cd /workspace/LiveTalking
git pull --ff-only
supervisorctl restart srs runtime-manager
```

Jika port publik Vast berubah setelah instance dibuat ulang, perbarui tiga hal:

1. `SRS_RTC_EIP` pada `.env.runtime` di Vast.
2. `RUNTIME_MANAGER_URL` pada `control-plane/.env` di aaPanel.
3. `RUNTIME_PUBLIC_URL` pada `control-plane/.env` di aaPanel.

Setelah mengubah env aaPanel, jalankan kembali `docker compose ... up -d
--force-recreate` agar template Nginx dan environment container diperbarui.

## Backup dan keamanan

- Gunakan volume Vast atau sinkronisasi object storage untuk model, avatar, dan
  konfigurasi penting. Container storage hilang ketika instance dihancurkan.
- Backup volume PostgreSQL secara terjadwal dari aaPanel.
- Jangan menjalankan `docker compose down -v` kecuali memang ingin menghapus
  database production.
- Jangan membuka PostgreSQL atau port `8080` aaPanel ke publik.
- Runtime Manager port publik wajib memakai token panjang. Bila memungkinkan,
  batasi akses port publik `8090` hanya dari IP VPS aaPanel.
- Setelah admin pertama berhasil dibuat, kosongkan `ADMIN_PASSWORD` pada env dan
  recreate container agar password bootstrap tidak terus tersimpan sebagai env.

Contoh backup PostgreSQL tanpa menghapus data:

```bash
mkdir -p /www/backup/aurora
docker compose --env-file control-plane/.env \
  -f infra/docker-compose.control.yml exec -T postgres \
  pg_dump -U livetalking -d livetalking \
  > /www/backup/aurora/livetalking-$(date +%F-%H%M).sql
```

## Referensi resmi

- [aaPanel Docker dan Compose](https://www.aapanel.com/docs/Function/Docker.html)
- [aaPanel Proxy Project dan SSL](https://www.aapanel.com/docs/Function/proxy.html)
- [Vast.ai Docker environment dan port mapping](https://docs.vast.ai/guides/instances/docker-environment)
- [Vast.ai container storage dan volumes](https://docs.vast.ai/guides/instances/storage/types)
