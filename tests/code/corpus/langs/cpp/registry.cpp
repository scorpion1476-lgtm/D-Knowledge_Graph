#include "shapes.hpp"

namespace geometry {

class Registry {
public:
    void add(int item) { count_ += item; }
    void seed() { add(1); }

private:
    int count_ = 0;
};

Registry makeRegistry() {
    return Registry();
}

}  // namespace geometry
