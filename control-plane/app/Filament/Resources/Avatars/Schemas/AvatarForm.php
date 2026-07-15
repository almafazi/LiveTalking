<?php

namespace App\Filament\Resources\Avatars\Schemas;

use Filament\Forms\Components\FileUpload;
use Filament\Forms\Components\Hidden;
use Filament\Forms\Components\KeyValue;
use Filament\Forms\Components\Select;
use Filament\Forms\Components\TextInput;
use Filament\Schemas\Components\Section;
use Filament\Schemas\Schema;
use Illuminate\Support\Str;

class AvatarForm
{
    public static function configure(Schema $schema): Schema
    {
        return $schema
            ->components([
                Section::make('Avatar source')
                    ->description('Upload a clean front-facing video. Processing runs during a maintenance window.')
                    ->schema([
                        TextInput::make('name')->required()->maxLength(120)
                            ->live(onBlur: true)
                            ->afterStateUpdated(fn ($state, $set) => $set('slug', Str::slug($state))),
                        TextInput::make('slug')->required()->alphaDash()->maxLength(120)
                            ->unique(ignoreRecord: true),
                        Select::make('model')->options(['wav2lip' => 'Wav2Lip'])->default('wav2lip')->required(),
                        FileUpload::make('source_path')->label('Source video')
                            ->disk(config('filesystems.avatar_disk'))
                            ->directory('avatars/sources')->acceptedFileTypes(['video/mp4', 'video/quicktime'])
                            ->maxSize(204800)->required(fn ($record) => $record === null),
                        Hidden::make('source_disk')->default(fn () => config('filesystems.avatar_disk')),
                        KeyValue::make('parameters')->default([
                            'img_size' => 256,
                            'pads' => '0 10 0 0',
                            'face_det_batch_size' => 16,
                            'teeth_suppression' => 25,
                        ])->keyLabel('Parameter')->valueLabel('Value'),
                    ])->columns(2),
            ]);
    }
}
