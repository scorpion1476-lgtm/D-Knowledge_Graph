import 'dart:math';

abstract class Shape {
  double area();
}

class Circle extends Shape {
  final double radius;

  Circle(this.radius);

  double area() {
    return pi * radius * radius;
  }
}

mixin Loggable {
  void log() {}
}

double totalArea(List<Shape> shapes) {
  double total = 0.0;
  for (final shape in shapes) {
    total += shape.area();
  }
  return total;
}

Circle makeCircle(double radius) {
  return Circle(radius);
}
