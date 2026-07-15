<?php

namespace App\Filament\Resources\ExperienceSettings\Schemas;

use Filament\Forms\Components\ColorPicker;
use Filament\Forms\Components\FileUpload;
use Filament\Forms\Components\Select;
use Filament\Forms\Components\Textarea;
use Filament\Forms\Components\TextInput;
use Filament\Schemas\Components\Section;
use Filament\Schemas\Schema;

class ExperienceSettingForm
{
    public static function configure(Schema $schema): Schema
    {
        return $schema
            ->components([
                Section::make('Public experience')->schema([
                    TextInput::make('brand_name')->required()->maxLength(80),
                    FileUpload::make('logo_path')->disk(config('filesystems.avatar_disk'))->directory('branding'),
                    Select::make('background')->options([
                        'purple' => 'Purple studio', 'black' => 'Black', 'green' => 'Green', 'white' => 'White',
                    ])->required(),
                    ColorPicker::make('accent_color')->required(),
                    Select::make('enabled_languages')->multiple()->options(['ms' => 'Bahasa Malaysia', 'en' => 'English'])
                        ->required(),
                    Textarea::make('welcome_text_ms')->rows(3),
                    Textarea::make('welcome_text_en')->rows(3),
                    Select::make('avatar_id')->relationship('avatar', 'name', modifyQueryUsing: fn ($query) => $query->where('status', 'ready'))
                        ->searchable()->preload()->required(),
                    Select::make('voice_preset_id')->relationship('voicePreset', 'name', modifyQueryUsing: fn ($query) => $query->where('enabled', true))
                        ->searchable()->preload()->required(),
                ])->columns(2),
            ]);
    }
}
