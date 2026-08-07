const std = @import("std");

const Circle = struct {
    radius: f64,

    pub fn area(self: Circle) f64 {
        return 3.14159 * self.radius * self.radius;
    }
};

const Kind = enum {
    round,
    square,
};

pub fn makeCircle(radius: f64) Circle {
    return Circle{ .radius = radius };
}

pub fn totalArea(shapes: []const Circle) f64 {
    var total: f64 = 0.0;
    for (shapes) |shape| {
        total += shape.area();
    }
    return total;
}

test "circle has area" {
    const c = makeCircle(1.0);
    try std.testing.expect(c.area() > 0.0);
}
