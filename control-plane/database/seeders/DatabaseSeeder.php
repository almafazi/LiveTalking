<?php

namespace Database\Seeders;

use App\Models\ExperienceSetting;
use App\Models\User;
use Illuminate\Database\Console\Seeds\WithoutModelEvents;
use Illuminate\Database\Seeder;

class DatabaseSeeder extends Seeder
{
    use WithoutModelEvents;

    /**
     * Seed the application's database.
     */
    public function run(): void
    {
        ExperienceSetting::current();

        if (env('ADMIN_PASSWORD')) {
            User::query()->updateOrCreate(
                ['email' => env('ADMIN_EMAIL', 'admin@example.com')],
                [
                    'name' => env('ADMIN_NAME', 'Administrator'),
                    'password' => env('ADMIN_PASSWORD'),
                ],
            );
        }
    }
}
