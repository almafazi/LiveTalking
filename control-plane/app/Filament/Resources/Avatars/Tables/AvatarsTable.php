<?php

namespace App\Filament\Resources\Avatars\Tables;

use App\Jobs\ProcessAvatar;
use Filament\Actions\Action;
use Filament\Actions\BulkActionGroup;
use Filament\Actions\DeleteBulkAction;
use Filament\Actions\EditAction;
use Filament\Tables\Columns\TextColumn;
use Filament\Tables\Table;

class AvatarsTable
{
    public static function configure(Table $table): Table
    {
        return $table
            ->columns([
                TextColumn::make('name')->searchable()->sortable(),
                TextColumn::make('model')->badge(),
                TextColumn::make('status')->badge()->color(fn (string $state) => match ($state) {
                    'ready' => 'success', 'failed' => 'danger', 'processing' => 'warning', default => 'gray',
                }),
                TextColumn::make('progress')->suffix('%'),
                TextColumn::make('processed_at')->dateTime()->sortable(),
            ])
            ->filters([
                //
            ])
            ->recordActions([
                Action::make('process')->label('Process avatar')->icon('heroicon-o-cpu-chip')
                    ->requiresConfirmation()
                    ->disabled(fn ($record) => ! $record->source_path || $record->status === 'processing')
                    ->action(fn ($record) => ProcessAvatar::dispatch($record->id)),
                EditAction::make(),
            ])
            ->toolbarActions([
                BulkActionGroup::make([
                    DeleteBulkAction::make(),
                ]),
            ]);
    }
}
