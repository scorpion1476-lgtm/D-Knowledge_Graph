import Foundation

protocol Drawable {
    func draw()
}

class Shape: Drawable {
    func draw() {
    }

    func area() -> Double {
        return 0.0
    }
}

class Circle: Shape {
    private let radius: Double

    init(radius: Double) {
        self.radius = radius
    }

    override func area() -> Double {
        return Double.pi * radius * radius
    }
}

struct Point {
    var x: Double
}

func makeCircle(radius: Double) -> Circle {
    return Circle(radius: radius)
}

func totalArea(shapes: [Shape]) -> Double {
    return shapes.reduce(0.0) { $0 + $1.area() }
}
