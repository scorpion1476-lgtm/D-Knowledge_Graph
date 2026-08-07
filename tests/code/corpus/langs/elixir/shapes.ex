defmodule Geometry.Shapes do
  import Kernel
  alias Geometry.Support

  defstruct radius: 0.0

  def area(radius) do
    3.14159 * radius * radius
  end

  def total_area(shapes) do
    Enum.reduce(shapes, 0.0, fn shape, acc -> acc + area(shape) end)
  end

  defp normalise(radius), do: radius
end
