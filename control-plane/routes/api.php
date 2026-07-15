<?php

use App\Http\Controllers\Api\PublicExperienceController;
use Illuminate\Support\Facades\Route;

Route::get('/public/config', [PublicExperienceController::class, 'config']);
Route::post('/public/conversation', [PublicExperienceController::class, 'conversation'])
    ->middleware('throttle:20,1');
