<?php

namespace App\Filament\Resources\Deployments\Schemas;

use Filament\Forms\Components\KeyValue;
use Filament\Forms\Components\Textarea;
use Filament\Forms\Components\TextInput;
use Filament\Schemas\Schema;

class DeploymentForm
{
    public static function configure(Schema $schema): Schema
    {
        return $schema
            ->components([
                TextInput::make('revision')->disabled(),
                TextInput::make('status')->disabled(),
                KeyValue::make('snapshot')->disabled(),
                KeyValue::make('health')->disabled(),
                Textarea::make('error_message')->disabled(),
            ]);
    }
}
