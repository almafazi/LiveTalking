<?php

namespace App\Filament\Resources\VoicePresets\Schemas;

use Filament\Forms\Components\TextInput;
use Filament\Forms\Components\Toggle;
use Filament\Schemas\Components\Section;
use Filament\Schemas\Schema;

class VoicePresetForm
{
    public static function configure(Schema $schema): Schema
    {
        return $schema
            ->components([
                Section::make('ElevenLabs voice preset')->schema([
                    TextInput::make('name')->required()->maxLength(120),
                    TextInput::make('voice_id')->required()->maxLength(120)->unique(ignoreRecord: true),
                    TextInput::make('model_id')->required()->default('eleven_turbo_v2_5'),
                    TextInput::make('preview_url')->url(),
                    Toggle::make('enabled')->default(true),
                ])->columns(2),
            ]);
    }
}
