<?php

namespace App\Filament\Resources\VoicePresets\Pages;

use App\Filament\Resources\VoicePresets\VoicePresetResource;
use Filament\Actions\DeleteAction;
use Filament\Resources\Pages\EditRecord;

class EditVoicePreset extends EditRecord
{
    protected static string $resource = VoicePresetResource::class;

    protected function getHeaderActions(): array
    {
        return [
            DeleteAction::make(),
        ];
    }
}
