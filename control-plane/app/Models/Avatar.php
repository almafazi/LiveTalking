<?php

namespace App\Models;

use Database\Factories\AvatarFactory;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class Avatar extends Model
{
    /** @use HasFactory<AvatarFactory> */
    use HasFactory;

    protected $guarded = [];

    protected function casts(): array
    {
        return [
            'parameters' => 'array',
            'processed_at' => 'datetime',
            'progress' => 'integer',
        ];
    }
}
