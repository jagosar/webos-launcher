---
paths:
  - "**/*.php"
  - "**/composer.json"
---
<!-- Adapted from ECC (rules/php), https://github.com/affaan-m/ECC — MIT License, Copyright (c) 2026 Affaan Mustafa. -->
# PHP

Extends the [common rules](common.md).

## Style

- PSR-12, with PHP-CS-Fixer or Laravel Pint.
- `declare(strict_types=1);`, scalar types, return types and typed properties in all new code.
- DTOs and value objects with `readonly` properties for data that crosses a boundary; arrays only for simple maps.
- Thin controllers and explicit services, with dependencies through the constructor.
- A `use` for every class referenced.

## Errors

- Exceptions for exceptional states; never `false` or `null` as a hidden error channel in new code.
- Input becomes a validated DTO before it reaches the domain.

## Tests

- PHPUnit or Pest, whichever the project uses.
- Fast unit tests, separate from integration tests with the framework or the database.
- Factories or builders to prepare data, not huge hand-written arrays.

## Security

- Prepared statements (PDO, Doctrine, Eloquent's query builder) for every dynamic query.
- `password_hash()` and `password_verify()`; regenerate the session on authentication.
- Escaping by default in templates. CSRF on every request that changes state.
- `composer audit` when `composer.lock` changes.

## Common commands

| `verify` phase | Typical command |
|---|---|
| build | `composer validate --strict` |
| types | `vendor/bin/phpstan analyse` |
| lint | `vendor/bin/php-cs-fixer fix --dry-run` or `vendor/bin/pint --test` |
| tests | `vendor/bin/phpunit` or `vendor/bin/pest` |
