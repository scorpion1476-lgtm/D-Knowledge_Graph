require_relative "shapes"

class Registry
  def initialize
    @items = []
  end

  def add(shape)
    @items << shape
  end

  def seed
    add(make_circle(1))
  end
end

def empty_registry
  Registry.new
end
