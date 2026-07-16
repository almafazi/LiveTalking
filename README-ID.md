# LiveTalking Aurora: lokal dan deployment

Panduan cepat:

- [Menjalankan secara lokal](#menjalankan-secara-lokal)
- [Deployment aaPanel + Vast.ai](docs/deployment-aapanel-vast.md)
- [Runbook troubleshooting GPU/Vast](WORKFLOW.md)

## Menjalankan secara lokal

Repository ini adalah monorepo yang terdiri dari:

- LiveTalking (Python) untuk rendering avatar dan lip-sync, port `8010`.
- SRS untuk publikasi dan playback WebRTC, port API `10100` dan RTC TCP `10200`.
- Runtime Manager (Python) untuk mengelola avatar/runtime, port `8090`.
- Control Plane (Laravel + Filament + React), port internal `18888`.
- Nginx sebagai satu pintu untuk UI dan media, port publik `18880`.

Setelah semua service hidup, buka:

- Tampilan publik: <http://127.0.0.1:18880/>
- Admin Filament: <http://127.0.0.1:18880/admin>

## Prasyarat

- Python 3.12
- PHP 8.3 atau lebih baru dan Composer
- Node.js dan npm
- Nginx
- SRS 6 (binary lokal atau Docker)

Model Wav2Lip harus tersedia di `models/wav2lip.pth`, sedangkan avatar bawaan
berada di `data/avatars/wav2lip256_avatar1`.

## Setup pertama kali

Jalankan dari root repository:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r runtime-manager/requirements.txt

cd control-plane
composer install
cp .env.example .env
php artisan key:generate
npm ci
```

Edit `control-plane/.env`, minimal isi bagian berikut:

```dotenv
APP_URL=http://127.0.0.1:18880

ELEVENLABS_API_KEY=isi_api_key_anda
ELEVENLABS_AGENT_ID=isi_agent_id_anda

RUNTIME_MANAGER_URL=http://127.0.0.1:8090
RUNTIME_MANAGER_TOKEN=ganti-dengan-token-random-yang-panjang

DB_CONNECTION=sqlite
ADMIN_NAME=Administrator
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=ganti-dengan-password-admin
```

Jangan commit file `.env`. Nilai `RUNTIME_MANAGER_TOKEN` harus sama pada
Laravel, Runtime Manager, dan proses LiveTalking.

Lanjutkan setup database dan frontend:

```bash
php artisan migrate --seed
npm run build
cd ..
```

Jika database sudah pernah dibuat sebelum `ADMIN_PASSWORD` diisi, jalankan
ulang `php artisan db:seed` dari folder `control-plane`.

## Menjalankan semua service

Gunakan terminal terpisah untuk setiap service berikut. Semua command dimulai
dari root repository, kecuali jika ada perintah `cd control-plane`.

### 1. SRS

Jika SRS sudah terpasang secara lokal:

```bash
/path/ke/srs/objs/srs -c "$PWD/deploy/srs-livetalking.conf"
```

Atau jalankan image resmi SRS dengan Docker:

```bash
docker run --rm --name livetalking-srs \
  -p 10100:10100 \
  -p 10200:10200 \
  -p 10001:10001/udp \
  -p 8088:8088 \
  -v "$PWD/deploy/srs-livetalking.conf:/usr/local/srs/conf/livetalking.conf:ro" \
  ossrs/srs:6 ./objs/srs -c conf/livetalking.conf
```

### 2. LiveTalking

Ganti token di bawah dengan nilai `RUNTIME_MANAGER_TOKEN` dari
`control-plane/.env`:

```bash
export RUNTIME_MANAGER_TOKEN='token-yang-sama-dengan-control-plane'
export SRS_RTC_EIP='127.0.0.1:10200'

.venv/bin/python app.py \
  --transport rtcpush \
  --model wav2lip \
  --avatar_id wav2lip256_avatar1 \
  --batch_size 4 \
  --max_session 1 \
  --push_url 'http://127.0.0.1:10100/rtc/v1/whip/?app=live&stream=livestream&eip=127.0.0.1'
```

Tunggu sampai log menunjukkan WHIP berhasil terhubung ke SRS.

### 3. Runtime Manager

```bash
export RUNTIME_MANAGER_TOKEN='token-yang-sama-dengan-control-plane'
export LIVETALKING_AUTOSTART=0
.venv/bin/python runtime-manager/manager.py
```

`LIVETALKING_AUTOSTART=0` digunakan saat LiveTalking dijalankan manual seperti
langkah nomor 2. Di server GPU, nilainya dapat diubah menjadi `1` agar Runtime
Manager yang menjalankan ulang proses LiveTalking.

### 4. Laravel

```bash
cd control-plane
php artisan serve --host=127.0.0.1 --port=18888
```

### 5. Laravel queue worker

Queue diperlukan untuk proses avatar dan publish dari panel admin.

```bash
cd control-plane
php artisan queue:work --tries=1 --timeout=1200
```

### 6. Nginx lokal

```bash
nginx -c "$PWD/infra/nginx.local.conf" -g 'daemon off;'
```

Nginx meneruskan halaman dan API ke Laravel, sedangkan seluruh request
`/media/*` diteruskan ke LiveTalking. UI harus dibuka melalui port `18880`,
bukan langsung melalui port Laravel `18888`, agar video avatar dapat dimuat.

## Cek kesehatan service

```bash
curl http://127.0.0.1:10100/api/v1/versions
curl http://127.0.0.1:8010/api/admin/config
curl http://127.0.0.1:18880/api/public/config

curl \
  -H 'Authorization: Bearer token-yang-sama-dengan-control-plane' \
  http://127.0.0.1:8090/internal/health
```

## Mengubah avatar dan audio

1. Login ke <http://127.0.0.1:18880/admin>.
2. Upload video avatar lalu jalankan aksi **Process avatar**.
3. Pastikan queue worker masih aktif sampai proses selesai.
4. Pilih avatar dan voice ElevenLabs pada **Experience Settings**.
5. Tekan **Publish**. Runtime Manager akan memasang revision dan me-restart
   LiveTalking bila autostart diaktifkan.

## Troubleshooting

### Halaman muncul tetapi avatar kosong

- Pastikan halaman dibuka dari `http://127.0.0.1:18880/`.
- Pastikan SRS, LiveTalking, Laravel, dan Nginx semuanya masih hidup.
- Pastikan `SRS_RTC_EIP=127.0.0.1:10200` saat LiveTalking dijalankan.
- Cek log LiveTalking untuk respons WHIP `201` dan cek stream SRS:

```bash
curl http://127.0.0.1:10100/api/v1/streams/
```

- Jika port `8010` tidak merespons, hentikan proses LiveTalking lama lalu
  jalankan kembali command pada langkah 2.

### Perubahan frontend belum terlihat

```bash
cd control-plane
npm run build
php artisan optimize:clear
```

Setelah itu lakukan hard refresh di browser.

### Admin tidak bisa login

Pastikan `ADMIN_EMAIL` dan `ADMIN_PASSWORD` sudah diisi, lalu jalankan:

```bash
cd control-plane
php artisan db:seed
```

## Deployment production

Gunakan panduan [`docs/deployment-aapanel-vast.md`](docs/deployment-aapanel-vast.md)
untuk menjalankan Laravel/PostgreSQL di aaPanel dan LiveTalking/SRS/Runtime
Manager di Vast.ai. Detail troubleshooting model dan transport GPU tersedia di
[`WORKFLOW.md`](WORKFLOW.md).
