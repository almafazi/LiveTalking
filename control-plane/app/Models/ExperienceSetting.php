<?php

namespace App\Models;

use Database\Factories\ExperienceSettingFactory;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

class ExperienceSetting extends Model
{
    /** @use HasFactory<ExperienceSettingFactory> */
    use HasFactory;

    protected $guarded = [];

    protected function casts(): array
    {
        return [
            'enabled_languages' => 'array',
            'maintenance' => 'boolean',
            'active_revision' => 'integer',
        ];
    }

    public function avatar(): BelongsTo
    {
        return $this->belongsTo(Avatar::class);
    }

    public function voicePreset(): BelongsTo
    {
        return $this->belongsTo(VoicePreset::class);
    }

    public static function current(): self
    {
        return self::query()->firstOrCreate([], [
            'welcome_text_ms' => 'Hai, saya Aurora. Tekan mikrofon dan tanyakan apa sahaja.',
            'welcome_text_en' => 'Hi, I am Aurora. Press the microphone and ask me anything.',
            'enabled_languages' => ['ms', 'en'],
        ]);
    }
}
