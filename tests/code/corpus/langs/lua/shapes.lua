local math = require("math")

local Shape = {}

function Shape.area(self)
  return 0.0
end

function Shape:describe()
  return tostring(self:area())
end

local Circle = {}

function Circle.area(self)
  return math.pi * self.radius * self.radius
end

local function makeCircle(radius)
  return { radius = radius }
end

function totalArea(shapes)
  local total = 0.0
  for _, shape in ipairs(shapes) do
    total = total + Circle.area(shape)
  end
  return total
end
