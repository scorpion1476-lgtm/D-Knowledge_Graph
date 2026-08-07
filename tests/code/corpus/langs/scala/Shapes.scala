package geometry

import scala.math.Pi

trait Drawable {
  def draw(): Unit
}

class Shape extends Drawable {
  def draw(): Unit = {}
  def area(): Double = 0.0
}

class Circle(radius: Double) extends Shape {
  override def area(): Double = Pi * radius * radius
}

object Factory {
  def makeCircle(radius: Double): Circle = new Circle(radius)
}

def totalArea(shapes: List[Shape]): Double = shapes.map(s => s.area()).sum
