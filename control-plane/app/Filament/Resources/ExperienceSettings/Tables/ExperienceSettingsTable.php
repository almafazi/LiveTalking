<?php

namespace App\Filament\Resources\ExperienceSettings\Tables;

use App\Jobs\PublishExperience;
use Filament\Actions\Action;
use Filament\Actions\BulkActionGroup;
use Filament\Actions\DeleteBulkAction;
use Filament\Actions\EditAction;
use Filament\Tables\Columns\IconColumn;
use Filament\Tables\Columns\TextColumn;
use Filament\Tables\Table;

class ExperienceSettingsTable
{
    public static function configure(Table $table): Table
    {
        return $table
            ->columns([
                TextColumn::make('brand_name'),
                TextColumn::make('avatar.name')->label('Avatar'),
                TextColumn::make('voicePreset.name')->label('Voice'),
                TextColumn::make('active_revision')->label('Active revision')->badge(),
                IconColumn::make('maintenance')->boolean(),
            ])
            ->filters([
                //
            ])
            ->recordActions([
                Action::make('publish')->label('Publish')->icon('heroicon-o-rocket-launch')
                    ->color('success')->requiresConfirmation()
                    ->action(fn ($record) => PublishExperience::dispatch($record->id)),
                EditAction::make(),
            ])
            ->toolbarActions([
                BulkActionGroup::make([
                    DeleteBulkAction::make(),
                ]),
            ]);
    }
}
