#include <vector>
#include <cmath>

namespace geometry {

class Shape {
public:
    virtual double area() const { return 0.0; }
};

class Circle : public Shape {
public:
    explicit Circle(double radius) : radius_(radius) {}
    double area() const override { return M_PI * radius_ * radius_; }

private:
    double radius_;
};

struct Point {
    double x;
    double y;
};

enum Kind { Round, Square };

double totalArea(const std::vector<Circle> &shapes) {
    double total = 0.0;
    for (const auto &shape : shapes) {
        total += shape.area();
    }
    return total;
}

}  // namespace geometry
