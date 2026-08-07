package geometry

import kotlin.math.PI

open class Shape {
    open fun area(): Double {
        return 0.0
    }

    fun describe(): String {
        return "shape of area " + area()
    }
}

class Circle(private val radius: Double) : Shape() {
    override fun area(): Double {
        return PI * radius * radius
    }
}

object Factory {
    fun makeCircle(radius: Double): Circle {
        return Circle(radius)
    }
}

fun totalArea(shapes: List<Shape>): Double {
    return shapes.sumOf { it.area() }
}
