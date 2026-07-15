<?php

namespace App\Filament\Resources\VoicePresets\Pages;

use App\Filament\Resources\VoicePresets\VoicePresetResource;
use Filament\Actions\CreateAction;
use Filament\Resources\Pages\ListRecords;

class ListVoicePresets extends ListRecords
{
    protected static string $resource = VoicePresetResource::class;

    protected function getHeaderActions(): array
    {
        return [
            CreateAction::make(),
        ];
    }
}
