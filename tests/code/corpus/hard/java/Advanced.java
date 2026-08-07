package advanced;

import java.util.function.Supplier;

public class Advanced {
    static class Nested {
        void ping() {}
    }

    interface Callback {
        void run();
    }

    static {
        setup();
    }

    private static void setup() {}

    public <T> T identity(T value) {
        return value;
    }

    public Supplier<String> lazy() {
        return () -> "x";
    }

    record Pair(int left, int right) {}
}
