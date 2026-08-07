interface Shape {
  area(): number;
}

class Rect {
  area(): number {
    return 4;
  }
  scale(): number {
    return this.area();
  }
}

class Square extends Rect {
  side(): number {
    return 2;
  }
}

function makeRect(): Rect {
  return new Rect();
}

function tsHelper(): number {
  return 7;
}
