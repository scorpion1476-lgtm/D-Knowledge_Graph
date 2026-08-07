# A small Ruby sample used by the custom-language worked example.
# The dkg.languages.json in this folder registers Ruby so the code plane parses
# this file into the shared code graph with no change to the platform itself.

class Greeter
  def initialize(name)
    @name = name
  end

  def greet
    self.format_message
  end

  def format_message
    puts "Hello"
  end
end

class LoudGreeter < Greeter
  def greet
    self.format_message
  end
end
