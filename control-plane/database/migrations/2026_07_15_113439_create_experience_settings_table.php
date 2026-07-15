<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Run the migrations.
     */
    public function up(): void
    {
        Schema::create('experience_settings', function (Blueprint $table) {
            $table->id();
            $table->string('brand_name')->default('AURORA');
            $table->string('logo_path')->nullable();
            $table->string('background')->default('purple');
            $table->string('accent_color', 16)->default('#a855f7');
            $table->text('welcome_text_ms')->nullable();
            $table->text('welcome_text_en')->nullable();
            $table->json('enabled_languages')->nullable();
            $table->foreignId('avatar_id')->nullable()->constrained()->nullOnDelete();
            $table->foreignId('voice_preset_id')->nullable()->constrained()->nullOnDelete();
            $table->unsignedBigInteger('active_revision')->default(0);
            $table->boolean('maintenance')->default(false);
            $table->string('maintenance_message')->nullable();
            $table->timestamps();
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::dropIfExists('experience_settings');
    }
};
