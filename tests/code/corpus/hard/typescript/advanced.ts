export namespace Shapes {
  export function area(): number {
    return 0;
  }
}

export abstract class Base<T> {
  abstract render(): T;
  protected helper(): number {
    return 1;
  }
}

export function identity<T>(value: T): T {
  return value;
}

export const arrowFn = (value: number): number => value * 2;

declare function ambient(value: string): void;

export class Impl extends Base<string> {
  render(): string {
    return "x";
  }
}
