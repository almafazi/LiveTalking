FROM node:24-bookworm-slim AS frontend
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY resources ./resources
COPY vite.config.js ./
COPY public ./public
RUN npm run build

FROM composer:2 AS vendor
WORKDIR /app
COPY composer.json composer.lock ./
RUN composer install --no-dev --prefer-dist --no-interaction --no-scripts --optimize-autoloader --ignore-platform-req=ext-intl

FROM php:8.4-fpm-alpine
RUN apk add --no-cache icu-dev libzip-dev postgresql-dev \
    && docker-php-ext-install intl opcache pdo_pgsql zip \
    && printf 'upload_max_filesize=220M\npost_max_size=220M\nmemory_limit=512M\nmax_execution_time=300\nmax_input_time=300\n' \
        > /usr/local/etc/php/conf.d/uploads.ini
WORKDIR /var/www/html
COPY . .
COPY --from=vendor /app/vendor ./vendor
COPY --from=frontend /app/public/build ./public/build
RUN php artisan package:discover --ansi
RUN mkdir -p storage/framework/cache storage/framework/sessions storage/framework/views storage/logs bootstrap/cache storage/app/private/livewire-tmp \
    && chown -R www-data:www-data storage bootstrap/cache
USER www-data
CMD ["php-fpm"]
