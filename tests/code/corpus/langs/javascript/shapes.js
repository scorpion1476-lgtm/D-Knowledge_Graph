import { PI } from "./constants.js";

export class Shape {
  area() {
    return 0;
  }
  describe() {
    return `shape of area ${this.area()}`;
  }
}

export class Circle extends Shape {
  constructor(radius) {
    super();
    this.radius = radius;
  }
  area() {
    return PI * this.radius * this.radius;
  }
}

export function makeCircle(radius) {
  return new Circle(radius);
}

function totalArea(shapes) {
  return shapes.reduce((sum, shape) => sum + shape.area(), 0);
}

export { totalArea };
