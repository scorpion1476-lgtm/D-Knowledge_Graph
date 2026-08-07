<?php

namespace App\Geometry;

use App\Support\Formatter;

interface Drawable
{
    public function draw();
}

trait Loggable
{
    public function log()
    {
        return true;
    }
}

class Shape implements Drawable
{
    public function draw()
    {
    }

    public function area()
    {
        return 0.0;
    }
}

class Circle extends Shape
{
    private $radius;

    public function __construct($radius)
    {
        $this->radius = $radius;
    }

    public function area()
    {
        return M_PI * $this->radius * $this->radius;
    }
}

function makeCircle($radius)
{
    return new Circle($radius);
}

function totalArea($shapes)
{
    $total = 0.0;
    foreach ($shapes as $shape) {
        $total += $shape->area();
    }
    return $total;
}
