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
        Schema::create('voice_presets', function (Blueprint $table) {
            $table->id();
            $table->string('name');
            $table->string('voice_id')->unique();
            $table->string('model_id')->default('eleven_turbo_v2_5');
            $table->string('preview_url')->nullable();
            $table->boolean('enabled')->default(true)->index();
            $table->timestamps();
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::dropIfExists('voice_presets');
    }
};
