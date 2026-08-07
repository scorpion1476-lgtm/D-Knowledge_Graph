<?php

namespace App\Advanced;

enum Status: string
{
    case Active = 'active';
    case Frozen = 'frozen';

    public function label(): string
    {
        return $this->value;
    }
}

abstract class Base
{
    abstract public function render(): string;

    public static function create(): static
    {
        return new static();
    }
}

final class Impl extends Base
{
    public function render(): string
    {
        return $this->helper();
    }

    private function helper(): string
    {
        return 'x';
    }
}

$anonymous = new class extends Base {
    public function render(): string
    {
        return 'anon';
    }
};

$arrow = fn ($x) => $x * 2;
