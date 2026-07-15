<?php

namespace App\Filament\Resources\Avatars\Pages;

use App\Filament\Resources\Avatars\AvatarResource;
use Filament\Actions\DeleteAction;
use Filament\Resources\Pages\EditRecord;

class EditAvatar extends EditRecord
{
    protected static string $resource = AvatarResource::class;

    protected function getHeaderActions(): array
    {
        return [
            DeleteAction::make(),
        ];
    }
}
