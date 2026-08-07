require "json"

module Geometry
  class Shape
    def area
      0.0
    end

    def describe
      "shape of area #{area}"
    end
  end

  class Circle < Shape
    def initialize(radius)
      @radius = radius
    end

    def area
      3.14159 * @radius * @radius
    end
  end
end

def make_circle(radius)
  Geometry::Circle.new(radius)
end

def total_area(shapes)
  shapes.sum { |shape| shape.area }
end
