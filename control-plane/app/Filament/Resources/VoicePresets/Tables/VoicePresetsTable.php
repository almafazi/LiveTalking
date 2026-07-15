<?php

namespace App\Filament\Resources\VoicePresets\Tables;

use Filament\Actions\Action;
use Filament\Actions\BulkActionGroup;
use Filament\Actions\DeleteBulkAction;
use Filament\Actions\EditAction;
use Filament\Tables\Columns\IconColumn;
use Filament\Tables\Columns\TextColumn;
use Filament\Tables\Table;

class VoicePresetsTable
{
    public static function configure(Table $table): Table
    {
        return $table
            ->columns([
                TextColumn::make('name')->searchable()->sortable(),
                TextColumn::make('voice_id')->copyable(),
                TextColumn::make('model_id')->badge(),
                IconColumn::make('enabled')->boolean(),
            ])
            ->filters([
                //
            ])
            ->recordActions([
                Action::make('preview')->icon('heroicon-o-play')->url(fn ($record) => $record->preview_url)
                    ->openUrlInNewTab()->visible(fn ($record) => filled($record->preview_url)),
                EditAction::make(),
            ])
            ->toolbarActions([
                BulkActionGroup::make([
                    DeleteBulkAction::make(),
                ]),
            ]);
    }
}
