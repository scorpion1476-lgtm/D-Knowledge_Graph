import { PI } from "./constants";

export interface Drawable {
  draw(): void;
}

export type Radius = number;

export enum Kind {
  Circle,
  Square,
}

export class Shape implements Drawable {
  draw(): void {}
  area(): number {
    return 0;
  }
}

export class Circle extends Shape {
  constructor(private radius: Radius) {
    super();
  }
  area(): number {
    return PI * this.radius * this.radius;
  }
}

export function makeCircle(radius: Radius): Circle {
  return new Circle(radius);
}

function totalArea(shapes: Shape[]): number {
  return shapes.reduce((sum, shape) => sum + shape.area(), 0);
}

export { totalArea };
