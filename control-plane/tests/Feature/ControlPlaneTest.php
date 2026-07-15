<?php

namespace Tests\Feature;

use App\Jobs\PublishExperience;
use App\Models\Avatar;
use App\Models\Deployment;
use App\Models\ExperienceSetting;
use App\Models\User;
use App\Models\VoicePreset;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Storage;
use Tests\TestCase;

class ControlPlaneTest extends TestCase
{
    use RefreshDatabase;

    public function test_public_config_exposes_only_public_active_fields(): void
    {
        ExperienceSetting::current();

        $this->getJson('/api/public/config')
            ->assertOk()
            ->assertJsonPath('data.brand_name', 'AURORA')
            ->assertJsonMissingPath('data.avatar.artifact_path')
            ->assertJsonMissingPath('data.voice.voice_id');
    }

    public function test_conversation_bootstrap_combines_provider_and_runtime_tokens(): void
    {
        ExperienceSetting::current();
        config()->set('services.elevenlabs.api_key', 'test-key');
        config()->set('services.elevenlabs.agent_id', 'agent-test');
        config()->set('services.runtime.token', 'runtime-test');

        Http::fake([
            'https://api.elevenlabs.io/*' => Http::response(['signed_url' => 'wss://example.test/conversation']),
            'http://127.0.0.1:8090/internal/audio-sessions' => Http::response([
                'control_token' => 'control-test', 'generation' => 0,
            ]),
        ]);

        $this->postJson('/api/public/conversation')
            ->assertOk()
            ->assertJsonPath('data.signed_url', 'wss://example.test/conversation')
            ->assertJsonPath('data.control_token', 'control-test');
    }

    public function test_authenticated_admin_resources_render(): void
    {
        $this->actingAs(User::factory()->create());
        ExperienceSetting::current();

        foreach (['/admin/avatars', '/admin/voice-presets', '/admin/experience-settings', '/admin/deployments'] as $path) {
            $this->get($path)->assertOk();
        }
    }

    public function test_publish_activates_revision_only_after_provider_and_runtime_are_healthy(): void
    {
        Storage::fake('local');
        config()->set('filesystems.avatar_disk', 'local');
        config()->set('services.elevenlabs.api_key', 'test-key');
        config()->set('services.elevenlabs.agent_id', 'agent-test');
        config()->set('services.runtime.token', 'runtime-test');

        Storage::disk('local')->put('avatars/artifacts/aurora.tar.gz', 'artifact');
        $avatar = Avatar::query()->create([
            'name' => 'Aurora', 'slug' => 'aurora', 'model' => 'wav2lip', 'status' => 'ready',
            'artifact_path' => 'avatars/artifacts/aurora.tar.gz', 'artifact_checksum' => hash('sha256', 'artifact'),
        ]);
        $voice = VoicePreset::query()->create([
            'name' => 'Aurora Voice', 'voice_id' => 'voice-test', 'model_id' => 'eleven-test', 'enabled' => true,
        ]);
        $settings = ExperienceSetting::current();
        $settings->update(['avatar_id' => $avatar->id, 'voice_preset_id' => $voice->id]);

        Http::fake(function ($request) {
            if (str_contains($request->url(), '/convai/agents/')) {
                return Http::response(['conversation_config' => ['tts' => ['voice_id' => 'voice-test']]]);
            }
            if (str_contains($request->url(), '/internal/deployments')) {
                return Http::response(['status' => 'healthy', 'health' => ['http_status' => 200]]);
            }

            return Http::response([], 404);
        });

        PublishExperience::dispatchSync($settings->id);

        $this->assertSame(1, $settings->fresh()->active_revision);
        $this->assertSame('healthy', Deployment::query()->first()->status);
        $this->assertFalse($settings->fresh()->maintenance);
    }
}
