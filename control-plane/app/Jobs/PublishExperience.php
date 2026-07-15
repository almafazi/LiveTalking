<?php

namespace App\Jobs;

use App\Models\Deployment;
use App\Models\ExperienceSetting;
use App\Services\ElevenLabsClient;
use App\Services\ExperienceSnapshot;
use App\Services\RuntimeManagerClient;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Queue\Queueable;
use Illuminate\Support\Facades\Storage;
use RuntimeException;
use Throwable;

class PublishExperience implements ShouldQueue
{
    use Queueable;

    public int $timeout = 1200;

    /**
     * Create a new job instance.
     */
    public function __construct(public int $settingsId) {}

    /**
     * Execute the job.
     */
    public function handle(RuntimeManagerClient $runtime, ElevenLabsClient $elevenLabs): void
    {
        $settings = ExperienceSetting::query()->findOrFail($this->settingsId);
        $snapshot = ExperienceSnapshot::from($settings);
        $previous = Deployment::query()->where('status', 'healthy')->latest('revision')->first()?->snapshot;
        $revision = ((int) Deployment::query()->max('revision')) + 1;
        $deployment = Deployment::query()->create([
            'revision' => $revision,
            'status' => 'applying',
            'snapshot' => $snapshot,
            'previous_snapshot' => $previous,
            'started_at' => now(),
        ]);
        $settings->update(['maintenance' => true, 'maintenance_message' => 'Menerapkan konfigurasi baru…']);

        try {
            if (! $snapshot['avatar'] || ! $snapshot['avatar']['artifact_path']) {
                throw new RuntimeException('Pilih avatar berstatus ready sebelum publish.');
            }

            if ($snapshot['voice']) {
                $elevenLabs->updateVoice($snapshot['voice']['voice_id'], $snapshot['voice']['model_id']);
                $verified = $elevenLabs->agent();
                $activeVoice = data_get($verified, 'conversation_config.tts.voice_id');
                if ($activeVoice !== $snapshot['voice']['voice_id']) {
                    throw new RuntimeException('ElevenLabs voice verification failed.');
                }
            }

            $disk = Storage::disk(config('filesystems.avatar_disk'));
            $artifact = $snapshot['avatar'];
            $runtimePayload = [
                'revision' => $revision,
                'avatar_id' => $artifact['slug'],
                'model' => $artifact['model'],
                'artifact_checksum' => $artifact['artifact_checksum'],
            ];
            if (config('filesystems.avatar_disk') === 's3') {
                $runtimePayload['artifact_url'] = $disk->temporaryUrl($artifact['artifact_path'], now()->addMinutes(30));
            } else {
                $runtimePayload['artifact_path'] = $disk->path($artifact['artifact_path']);
            }

            $result = $runtime->deploy($runtimePayload);
            if (($result['status'] ?? null) !== 'healthy') {
                throw new RuntimeException($result['error'] ?? 'Runtime deployment is not healthy.');
            }

            $deployment->update([
                'status' => 'healthy',
                'health' => $result['health'] ?? $runtime->health(),
                'finished_at' => now(),
            ]);
            $settings->update(['active_revision' => $revision]);
        } catch (Throwable $error) {
            if ($previous && data_get($previous, 'voice.voice_id')) {
                try {
                    $elevenLabs->updateVoice(data_get($previous, 'voice.voice_id'), data_get($previous, 'voice.model_id'));
                } catch (Throwable) {
                    // Preserve the original deployment failure in the audit log.
                }
            }
            $deployment->update([
                'status' => 'failed',
                'error_message' => $error->getMessage(),
                'finished_at' => now(),
            ]);
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
