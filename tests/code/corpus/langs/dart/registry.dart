import 'shapes.dart';

class Registry {
  final List<Object> items = [];

  void add(Object item) {
    items.add(item);
  }

  void seed() {
    add(makeCircle(1.0));
  }
}

Registry emptyRegistry() {
  return Registry();
}
