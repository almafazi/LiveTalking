<?php

namespace App\Filament\Resources\ExperienceSettings\Pages;

use App\Filament\Resources\ExperienceSettings\ExperienceSettingResource;
use Filament\Actions\CreateAction;
use Filament\Resources\Pages\ListRecords;

class ListExperienceSettings extends ListRecords
{
    protected static string $resource = ExperienceSettingResource::class;

    protected function getHeaderActions(): array
    {
        return [
            CreateAction::make()->visible(fn () => static::getModel()::query()->doesntExist()),
        ];
    }
}
