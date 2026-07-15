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
        Schema::create('avatars', function (Blueprint $table) {
            $table->id();
            $table->string('name');
            $table->string('slug')->unique();
            $table->string('model')->default('wav2lip');
            $table->string('source_disk')->default('local');
            $table->string('source_path')->nullable();
            $table->string('artifact_path')->nullable();
            $table->string('artifact_checksum', 64)->nullable();
            $table->string('preview_path')->nullable();
            $table->string('runtime_task_id')->nullable()->index();
            $table->string('status')->default('draft')->index();
            $table->unsignedTinyInteger('progress')->default(0);
            $table->json('parameters')->nullable();
            $table->text('error_message')->nullable();
            $table->timestamp('processed_at')->nullable();
            $table->timestamps();
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::dropIfExists('avatars');
    }
};
