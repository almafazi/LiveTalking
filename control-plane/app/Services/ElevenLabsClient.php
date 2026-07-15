<?php

namespace App\Services;

use Illuminate\Http\Client\PendingRequest;
use Illuminate\Support\Facades\Http;
use RuntimeException;

class ElevenLabsClient
{
    private function client(): PendingRequest
    {
        $key = (string) config('services.elevenlabs.api_key');
        if ($key === '') {
            throw new RuntimeException('ELEVENLABS_API_KEY is not configured.');
        }

        return Http::baseUrl(rtrim((string) config('services.elevenlabs.base_url'), '/'))
            ->acceptJson()->withHeaders(['xi-api-key' => $key])
            ->timeout(20)->retry(2, 400);
    }

    public function agent(): array
    {
        return $this->client()->get('/convai/agents/'.$this->agentId())->throw()->json();
    }

    public function updateVoice(string $voiceId, string $modelId): array
    {
        return $this->client()->patch('/convai/agents/'.$this->agentId(), [
            'conversation_config' => [
                'tts' => [
                    'voice_id' => $voiceId,
                    'model_id' => $modelId,
                    'agent_output_audio_format' => 'pcm_24000',
                ],
                'asr' => ['user_input_audio_format' => 'pcm_16000'],
            ],
        ])->throw()->json();
    }

    public function signedUrl(): string
    {
        $response = $this->client()->get('/convai/conversation/get-signed-url', [
            'agent_id' => $this->agentId(),
        ])->throw()->json();

        return $response['signed_url'] ?? throw new RuntimeException('ElevenLabs did not return signed_url.');
    }

    private function agentId(): string
    {
        return (string) config('services.elevenlabs.agent_id')
            ?: throw new RuntimeException('ELEVENLABS_AGENT_ID is not configured.');
    }
}
