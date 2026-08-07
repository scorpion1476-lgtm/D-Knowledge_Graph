#include <stdio.h>
#include "shapes.h"

struct Circle {
    double radius;
};

union Value {
    long i;
    double d;
};

enum Kind {
    ROUND,
    SQUARE
};

typedef struct Circle CircleAlias;

double circle_area(struct Circle *c) {
    return 3.14159 * c->radius * c->radius;
}

double total_area(struct Circle *shapes, int count) {
    double total = 0.0;
    for (int i = 0; i < count; i++) {
        total += circle_area(&shapes[i]);
    }
    return total;
}

void report(struct Circle *c) {
    printf("%f\n", circle_area(c));
}
