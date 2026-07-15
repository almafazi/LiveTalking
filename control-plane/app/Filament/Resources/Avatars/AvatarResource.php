<?php

namespace App\Filament\Resources\Avatars;

use App\Filament\Resources\Avatars\Pages\CreateAvatar;
use App\Filament\Resources\Avatars\Pages\EditAvatar;
use App\Filament\Resources\Avatars\Pages\ListAvatars;
use App\Filament\Resources\Avatars\Schemas\AvatarForm;
use App\Filament\Resources\Avatars\Tables\AvatarsTable;
use App\Models\Avatar;
use BackedEnum;
use Filament\Resources\Resource;
use Filament\Schemas\Schema;
use Filament\Support\Icons\Heroicon;
use Filament\Tables\Table;

class AvatarResource extends Resource
{
    protected static ?string $model = Avatar::class;

    protected static string|BackedEnum|null $navigationIcon = Heroicon::OutlinedRectangleStack;

    public static function form(Schema $schema): Schema
    {
        return AvatarForm::configure($schema);
    }

    public static function table(Table $table): Table
    {
        return AvatarsTable::configure($table);
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
            'index' => ListAvatars::route('/'),
            'create' => CreateAvatar::route('/create'),
            'edit' => EditAvatar::route('/{record}/edit'),
        ];
    }
}
