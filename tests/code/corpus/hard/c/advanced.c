#include <stdlib.h>

typedef int (*Comparator)(const void *, const void *);

static int compare_ints(const void *a, const void *b) {
    return *(const int *)a - *(const int *)b;
}

struct Node {
    struct Node *next;
    int value;
};

inline int doubled(int x) {
    return x * 2;
}

int *allocate(int count) {
    return malloc(sizeof(int) * count);
}

void sort_all(int *values, int count, Comparator cmp) {
    qsort(values, count, sizeof(int), cmp);
}
