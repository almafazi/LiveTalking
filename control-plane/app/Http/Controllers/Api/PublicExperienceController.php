<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Models\Deployment;
use App\Models\ExperienceSetting;
use App\Services\ElevenLabsClient;
use App\Services\ExperienceSnapshot;
use App\Services\RuntimeManagerClient;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\Storage;

class PublicExperienceController extends Controller
{
    public function config(): JsonResponse
    {
        $settings = ExperienceSetting::current();
        $active = Deployment::query()
            ->where('revision', $settings->active_revision)
            ->where('status', 'healthy')
            ->first();
        $snapshot = $active?->snapshot ?: ExperienceSnapshot::from($settings);

        if (! empty($snapshot['logo_path'])) {
            $snapshot['logo_url'] = Storage::disk(config('filesystems.avatar_disk'))
                ->url($snapshot['logo_path']);
        }
        unset(
            $snapshot['logo_path'],
            $snapshot['voice'],
            $snapshot['avatar']['artifact_path'],
            $snapshot['avatar']['artifact_checksum'],
        );

        return response()->json([
            'data' => array_merge($snapshot, [
                'revision' => $settings->active_revision,
                'maintenance' => $settings->maintenance,
                'maintenance_message' => $settings->maintenance_message,
                'media' => [
                    'whep_url' => config('app.url').'/media/srs-whep/rtc/v1/whep/?app=live&stream=livestream',
                    'flv_url' => config('app.url').'/media/srs-live/live/livestream.flv',
                ],
            ]),
        ])->header('Cache-Control', 'no-store');
    }

    public function conversation(
        ElevenLabsClient $elevenLabs,
        RuntimeManagerClient $runtime,
    ): JsonResponse {
        $settings = ExperienceSetting::current();
        abort_if($settings->maintenance, 503, $settings->maintenance_message ?: 'Service is in maintenance.');

        if (config('app.only_embed')) {
            $data = [
                'conversation_transport' => 'browser',
                'signed_url' => $elevenLabs->signedUrl(),
            ];
        } elseif (config('app.engine_convai')) {
            // Engine opens the ConvAI socket itself; the browser never needs
            // the signed URL and never relays avatar audio.
            $data = array_merge(
                $runtime->createAudioSession(),
                ['conversation_transport' => 'engine'],
            );
        } else {
            $data = array_merge(
                $runtime->createAudioSession(),
                [
                    'conversation_transport' => 'legacy',
                    'signed_url' => $elevenLabs->signedUrl(),
                ],
            );
        }

        return response()->json(['data' => $data])->header('Cache-Control', 'no-store');
    }
}
