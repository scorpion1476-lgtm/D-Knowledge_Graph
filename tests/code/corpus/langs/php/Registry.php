<?php

namespace App\Geometry;

class Registry
{
    private $items = [];

    public function add($item)
    {
        $this->items[] = $item;
    }

    public function seed()
    {
        $this->add(makeCircle(1.0));
    }
}

function emptyRegistry()
{
    return new Registry();
}
