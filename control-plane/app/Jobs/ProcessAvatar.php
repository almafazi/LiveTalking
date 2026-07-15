<?php

namespace App\Jobs;

use App\Models\Avatar;
use App\Models\ExperienceSetting;
use App\Services\RuntimeManagerClient;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Queue\Queueable;
use Illuminate\Support\Facades\Storage;
use RuntimeException;
use Throwable;

class ProcessAvatar implements ShouldQueue
{
    use Queueable;

    public int $timeout = 1200;

    /**
     * Create a new job instance.
     */
    public function __construct(public int $avatarId) {}

    /**
     * Execute the job.
     */
    public function handle(RuntimeManagerClient $runtime): void
    {
        $avatar = Avatar::query()->findOrFail($this->avatarId);
        $settings = ExperienceSetting::current();
        $diskName = $avatar->source_disk ?: config('filesystems.avatar_disk');
        $disk = Storage::disk($diskName);

        $settings->update([
            'maintenance' => true,
            'maintenance_message' => 'Avatar baru sedang diproses. Layanan akan kembali otomatis.',
        ]);
        $avatar->update(['status' => 'processing', 'progress' => 0, 'error_message' => null]);

        try {
            $payload = [
                'avatar_id' => $avatar->slug,
                'model' => $avatar->model,
                'parameters' => $avatar->parameters ?: [],
            ];

            if ($diskName === 's3') {
                $payload['source_url'] = $disk->temporaryUrl($avatar->source_path, now()->addMinutes(30));
                $artifactPath = 'avatars/artifacts/'.$avatar->slug.'.tar.gz';
                $upload = $disk->temporaryUploadUrl($artifactPath, now()->addMinutes(60));
                $payload['artifact_upload_url'] = $upload['url'];
                $payload['artifact_upload_headers'] = $upload['headers'];
                $payload['artifact_path'] = $artifactPath;
            } else {
                $payload['source_path'] = $disk->path($avatar->source_path);
                $payload['artifact_destination'] = $disk->path('avatars/artifacts/'.$avatar->slug.'.tar.gz');
                $payload['artifact_path'] = 'avatars/artifacts/'.$avatar->slug.'.tar.gz';
            }

            $task = $runtime->createAvatarJob($payload);
            $taskId = (string) ($task['task_id'] ?? '');
            if ($taskId === '') {
                throw new RuntimeException('Runtime manager did not return task_id.');
            }
            $avatar->update(['runtime_task_id' => $taskId]);

            $deadline = now()->addSeconds((int) config('services.runtime.timeout'));
            do {
                sleep(2);
                $task = $runtime->avatarJob($taskId);
                $avatar->update(['progress' => (int) ($task['progress'] ?? 0)]);
                if (in_array($task['status'] ?? null, ['completed', 'failed'], true)) {
                    break;
                }
            } while (now()->lessThan($deadline));

            if (($task['status'] ?? null) !== 'completed') {
                throw new RuntimeException($task['error'] ?? 'Avatar processing timed out.');
            }

            $avatar->update([
                'status' => 'ready',
                'progress' => 100,
                'artifact_path' => $task['artifact_path'] ?? $payload['artifact_path'],
                'artifact_checksum' => $task['artifact_checksum'] ?? null,
                'processed_at' => now(),
            ]);
        } catch (Throwable $error) {
            $avatar->update(['status' => 'failed', 'error_message' => $error->getMessage()]);
            throw $error;
        } finally {
            $settings->update(['maintenance' => false, 'maintenance_message' => null]);
        }
    }

    public function failed(?Throwable $error): void
    {
        ExperienceSetting::current()->update(['maintenance' => false, 'maintenance_message' => null]);
    }
}
