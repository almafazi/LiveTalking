# Aurora control plane

Folder ini berisi aplikasi Laravel, Filament, dan React untuk mengelola avatar,
voice ElevenLabs, branding, serta tampilan publik Aurora.

Panduan setup dan cara menjalankan seluruh monorepo tersedia di
[`../README-ID.md`](../README-ID.md).

Panduan deployment production aaPanel + Vast.ai tersedia di
[`../docs/deployment-aapanel-vast.md`](../docs/deployment-aapanel-vast.md).

Untuk pengembangan frontend saja, setelah service backend utama hidup:

```bash
cd control-plane
npm run dev
```

Untuk menjalankan test Laravel:

```bash
cd control-plane
php artisan test
```
