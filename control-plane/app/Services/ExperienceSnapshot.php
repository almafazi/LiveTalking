<?php

namespace App\Services;

use App\Models\ExperienceSetting;

class ExperienceSnapshot
{
    public static function from(ExperienceSetting $settings): array
    {
        $settings->loadMissing(['avatar', 'voicePreset']);

        return [
            'brand_name' => $settings->brand_name,
            'logo_path' => $settings->logo_path,
            'background' => $settings->background,
            'accent_color' => $settings->accent_color,
            'welcome_text_ms' => $settings->welcome_text_ms,
            'welcome_text_en' => $settings->welcome_text_en,
            'enabled_languages' => $settings->enabled_languages ?: ['ms', 'en'],
            'avatar' => $settings->avatar ? [
                'id' => $settings->avatar->id,
                'slug' => $settings->avatar->slug,
                'model' => $settings->avatar->model,
                'artifact_path' => $settings->avatar->artifact_path,
                'artifact_checksum' => $settings->avatar->artifact_checksum,
            ] : null,
            'voice' => $settings->voicePreset ? [
                'id' => $settings->voicePreset->id,
                'voice_id' => $settings->voicePreset->voice_id,
                'model_id' => $settings->voicePreset->model_id,
            ] : null,
        ];
    }
}
