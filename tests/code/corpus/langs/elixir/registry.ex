defmodule Geometry.Registry do
  require Logger

  def add(items, item) do
    [item | items]
  end

  def seed(items) do
    add(items, 1)
  end
end
