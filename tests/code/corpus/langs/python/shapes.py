"""Geometry helpers used by the parsing corpus."""
import math
from dataclasses import dataclass


class Shape:
    def area(self):
        return 0.0

    def describe(self):
        return f"shape of area {self.area()}"


class Circle(Shape):
    def __init__(self, radius):
        self.radius = radius

    def area(self):
        return math.pi * self.radius ** 2


@dataclass
class Point:
    x: float
    y: float


def make_circle(radius):
    return Circle(radius)


def total_area(shapes):
    return sum(shape.area() for shape in shapes)
