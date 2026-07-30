<?php

use Illuminate\Support\Facades\Route;

Route::get('/{path?}', function () {
    return view(env('ONLY_EMBED') ? 'embed' : 'app');
})->where('path', '^(?!admin|api|up|storage).*$');
