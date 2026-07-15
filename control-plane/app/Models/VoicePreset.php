<?php

namespace App\Models;

use Database\Factories\VoicePresetFactory;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class VoicePreset extends Model
{
    /** @use HasFactory<VoicePresetFactory> */
    use HasFactory;

    protected $guarded = [];

    protected function casts(): array
    {
        return ['enabled' => 'boolean'];
    }
}
