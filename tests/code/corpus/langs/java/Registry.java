package geometry;

import java.util.ArrayList;

class Registry {
    private final ArrayList<Object> items = new ArrayList<>();

    Registry() {}

    void add(Object item) {
        items.add(item);
    }

    void seed() {
        add(Factory.makeCircle(1.0));
    }
}
