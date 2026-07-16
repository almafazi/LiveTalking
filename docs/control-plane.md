# LiveTalking monorepo control plane

For the complete production procedure using aaPanel for the control plane and
Vast.ai for the GPU runtime, see
[`deployment-aapanel-vast.md`](deployment-aapanel-vast.md).

The repository now contains three cooperating parts:

- The existing Python/GPU runtime at the repository root.
- `control-plane/`: Laravel 13, Filament 5, and the React public experience.
- `runtime-manager/`: an authenticated supervisor for avatar preprocessing,
  artifact installation, restart, health checks, and rollback.

## Local control-plane setup

```sh
cd control-plane
cp .env.example .env
php artisan key:generate
php artisan migrate --seed
npm ci
npm run build
php artisan serve
```

Set `ADMIN_PASSWORD` before seeding to create the first Filament administrator.
The admin panel is available at `/admin`; the interactive experience is `/`.
Run the queue in another terminal:

```sh
php artisan queue:work --tries=1 --timeout=1200
```

## Runtime manager

Use the same strong random `RUNTIME_MANAGER_TOKEN` in LiveTalking, the runtime
manager, and the Laravel control plane.

```sh
export RUNTIME_MANAGER_TOKEN='replace-with-a-long-random-token'
export LIVETALKING_AUTOSTART=0
.venv/bin/python runtime-manager/manager.py
```

Set `LIVETALKING_AUTOSTART=1` on the GPU host. Override
`LIVETALKING_COMMAND_TEMPLATE` when the SRS push URL or Python path differs.
The manager binds to loopback by default; expose it only through a private
network or firewall allowlist.

## Production topology

The VPS runs Laravel, PostgreSQL, its queue worker, Nginx, and S3-compatible
storage credentials. The GPU host runs SRS, the runtime manager, and the
LiveTalking child process. Nginx proxies `/media/*` to the public LiveTalking
endpoint while Laravel talks to the private runtime-manager endpoint.

`infra/docker-compose.control.yml` is the control-plane Compose definition.
Before starting it, configure `control-plane/.env`, `DB_PASSWORD`, and
`RUNTIME_PUBLIC_URL`, then run migrations and seed the administrator.

## Publish behavior

1. Upload a video and select **Process avatar** in Filament.
2. The queue enters maintenance, asks the GPU manager to preprocess Wav2Lip,
   stores the archive/checksum, and restores the current live avatar.
3. Select the ready avatar and an ElevenLabs voice preset in Experience Settings.
4. **Publish** patches and verifies the voice, installs the avatar revision,
   restarts LiveTalking, and activates public branding only after health passes.
5. A failed restart restores the previous avatar and voice and leaves an audit
   entry in Deployments.

For production, set `AVATAR_DISK=s3` and the normal `AWS_*` variables. Local
disk mode is intended for development where Laravel and the runtime manager
share the same filesystem.
