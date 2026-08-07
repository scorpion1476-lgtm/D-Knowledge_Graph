using LinearAlgebra
import Base: show

abstract type Shape end

struct Circle <: Shape
    radius::Float64
end

struct Point
    x::Float64
end

function area(c::Circle)
    return pi * c.radius * c.radius
end

function total_area(shapes)
    total = 0.0
    for shape in shapes
        total += area(shape)
    end
    return total
end

make_circle(radius) = Circle(radius)
