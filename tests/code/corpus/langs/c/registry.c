#include "shapes.h"

struct Registry {
    int count;
};

void registry_add(struct Registry *r) {
    r->count = r->count + 1;
}

void registry_seed(struct Registry *r) {
    registry_add(r);
}
