import { makeCircle } from "./shapes.js";

export class Registry {
  constructor() {
    this.items = [];
  }
  add(shape) {
    this.items.push(shape);
  }
  seed() {
    this.add(makeCircle(1));
  }
}

export function emptyRegistry() {
  return new Registry();
}
