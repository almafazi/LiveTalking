<?php

namespace App\Filament\Resources\Deployments\Tables;

use Filament\Actions\BulkActionGroup;
use Filament\Actions\DeleteBulkAction;
use Filament\Actions\EditAction;
use Filament\Tables\Columns\TextColumn;
use Filament\Tables\Table;

class DeploymentsTable
{
    public static function configure(Table $table): Table
    {
        return $table
            ->columns([
                TextColumn::make('revision')->sortable(),
                TextColumn::make('status')->badge()->color(fn (string $state) => match ($state) {
                    'healthy' => 'success', 'failed' => 'danger', 'applying' => 'warning', default => 'gray',
                }),
                TextColumn::make('started_at')->dateTime()->sortable(),
                TextColumn::make('finished_at')->dateTime(),
                TextColumn::make('error_message')->limit(60)->wrap(),
            ])
            ->filters([
                //
            ])
            ->recordActions([
                EditAction::make(),
            ])
            ->toolbarActions([
                BulkActionGroup::make([
                    DeleteBulkAction::make(),
                ]),
            ]);
    }
}
