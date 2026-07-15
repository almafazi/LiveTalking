<?php

namespace App\Services;

use Illuminate\Http\Client\PendingRequest;
use Illuminate\Support\Facades\Http;
use RuntimeException;

class RuntimeManagerClient
{
    private function client(): PendingRequest
    {
        $token = (string) config('services.runtime.token');
        if ($token === '') {
            throw new RuntimeException('RUNTIME_MANAGER_TOKEN is not configured.');
        }

        return Http::baseUrl(rtrim((string) config('services.runtime.base_url'), '/'))
            ->acceptJson()->withToken($token)
            ->timeout((int) config('services.runtime.timeout'))->retry(2, 500);
    }

    public function createAvatarJob(array $payload): array
    {
        return $this->client()->post('/internal/avatar-jobs', $payload)->throw()->json();
    }

    public function avatarJob(string $taskId): array
    {
        return $this->client()->get("/internal/avatar-jobs/{$taskId}")->throw()->json();
    }

    public function deploy(array $payload): array
    {
        return $this->client()->post('/internal/deployments', $payload)->throw()->json();
    }

    public function health(): array
    {
        return $this->client()->get('/internal/health')->throw()->json();
    }

    public function createAudioSession(): array
    {
        return $this->client()->post('/internal/audio-sessions')->throw()->json();
    }
}
