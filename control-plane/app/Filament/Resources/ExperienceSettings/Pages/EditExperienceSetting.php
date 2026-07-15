<?php

namespace App\Filament\Resources\ExperienceSettings\Pages;

use App\Filament\Resources\ExperienceSettings\ExperienceSettingResource;
use Filament\Resources\Pages\EditRecord;

class EditExperienceSetting extends EditRecord
{
    protected static string $resource = ExperienceSettingResource::class;

    protected function getHeaderActions(): array
    {
        return [
            // Singleton configuration cannot be deleted.
        ];
    }
}
