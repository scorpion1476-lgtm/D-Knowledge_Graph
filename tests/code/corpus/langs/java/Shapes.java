package geometry;

import java.util.List;

public interface Drawable {
    void draw();
}

class Shape implements Drawable {
    public void draw() {}

    public double area() {
        return 0.0;
    }
}

class Circle extends Shape {
    private final double radius;

    Circle(double radius) {
        this.radius = radius;
    }

    public double area() {
        return Math.PI * radius * radius;
    }
}

enum Kind {
    ROUND,
    SQUARE
}

class Factory {
    static Circle makeCircle(double radius) {
        return new Circle(radius);
    }

    static double totalArea(List<Shape> shapes) {
        double total = 0.0;
        for (Shape shape : shapes) {
            total += shape.area();
        }
        return total;
    }
}
