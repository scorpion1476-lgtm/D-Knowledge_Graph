local math = require("math")

type Circle = { radius: number }

type Kind = "round" | "square"

local function makeCircle(radius: number): Circle
  return { radius = radius }
end

function area(c: Circle): number
  return math.pi * c.radius * c.radius
end

function totalArea(shapes: {Circle}): number
  local total = 0.0
  for _, shape in ipairs(shapes) do
    total = total + area(shape)
  end
  return total
end
