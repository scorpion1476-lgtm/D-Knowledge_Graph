class Animal
  def eat
    1
  end
end

class Dog < Animal
  def bark
    eat
  end
end

def make_dog
  Dog.new
end

def ruby_helper
  7
end
