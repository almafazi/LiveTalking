<?php

namespace App\Filament\Resources\VoicePresets;

use App\Filament\Resources\VoicePresets\Pages\CreateVoicePreset;
use App\Filament\Resources\VoicePresets\Pages\EditVoicePreset;
use App\Filament\Resources\VoicePresets\Pages\ListVoicePresets;
use App\Filament\Resources\VoicePresets\Schemas\VoicePresetForm;
use App\Filament\Resources\VoicePresets\Tables\VoicePresetsTable;
use App\Models\VoicePreset;
use BackedEnum;
use Filament\Resources\Resource;
use Filament\Schemas\Schema;
use Filament\Support\Icons\Heroicon;
use Filament\Tables\Table;

class VoicePresetResource extends Resource
{
    protected static ?string $model = VoicePreset::class;

    protected static string|BackedEnum|null $navigationIcon = Heroicon::OutlinedRectangleStack;

    public static function form(Schema $schema): Schema
    {
        return VoicePresetForm::configure($schema);
    }

    public static function table(Table $table): Table
    {
        return VoicePresetsTable::configure($table);
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
            'index' => ListVoicePresets::route('/'),
            'create' => CreateVoicePreset::route('/create'),
            'edit' => EditVoicePreset::route('/{record}/edit'),
        ];
    }
}
