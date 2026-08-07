import { makeCircle } from "./shapes";

export type Slot = { index: number };

export class Registry {
  private items: unknown[] = [];
  add(item: unknown): void {
    this.items.push(item);
  }
  seed(): void {
    this.add(makeCircle(1));
  }
}

export function emptyRegistry(): Registry {
  return new Registry();
}
