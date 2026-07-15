<?php

namespace App\Filament\Resources\Avatars\Pages;

use App\Filament\Resources\Avatars\AvatarResource;
use Filament\Actions\CreateAction;
use Filament\Resources\Pages\ListRecords;

class ListAvatars extends ListRecords
{
    protected static string $resource = AvatarResource::class;

    protected function getHeaderActions(): array
    {
        return [
            CreateAction::make(),
        ];
    }
}
