<?php

namespace App\Filament\Resources\ExperienceSettings;

use App\Filament\Resources\ExperienceSettings\Pages\CreateExperienceSetting;
use App\Filament\Resources\ExperienceSettings\Pages\EditExperienceSetting;
use App\Filament\Resources\ExperienceSettings\Pages\ListExperienceSettings;
use App\Filament\Resources\ExperienceSettings\Schemas\ExperienceSettingForm;
use App\Filament\Resources\ExperienceSettings\Tables\ExperienceSettingsTable;
use App\Models\ExperienceSetting;
use BackedEnum;
use Filament\Resources\Resource;
use Filament\Schemas\Schema;
use Filament\Support\Icons\Heroicon;
use Filament\Tables\Table;

class ExperienceSettingResource extends Resource
{
    protected static ?string $model = ExperienceSetting::class;

    protected static string|BackedEnum|null $navigationIcon = Heroicon::OutlinedRectangleStack;

    public static function form(Schema $schema): Schema
    {
        return ExperienceSettingForm::configure($schema);
    }

    public static function table(Table $table): Table
    {
        return ExperienceSettingsTable::configure($table);
    }

    public static function getRelations(): array
    {
        return [
            //
        ];
    }

    public static function getPages(): array
    {
        return [
            'index' => ListExperienceSettings::route('/'),
            'create' => CreateExperienceSetting::route('/create'),
            'edit' => EditExperienceSetting::route('/{record}/edit'),
        ];
    }
}
