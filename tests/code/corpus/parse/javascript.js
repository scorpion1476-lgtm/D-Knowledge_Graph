import fs from "fs";

class Shape {
  perimeter() {
    return 0;
  }
}

class Circle extends Shape {
  radius() {
    return 3;
  }
}

function makeCircle() {
  return new Circle();
}

function jsHelper() {
  return 1;
}
