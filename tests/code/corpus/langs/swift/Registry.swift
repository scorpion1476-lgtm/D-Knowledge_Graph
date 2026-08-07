import Foundation

class Registry {
    private var items: [Any] = []

    func add(item: Any) {
        items.append(item)
    }

    func seed() {
        add(item: makeCircle(radius: 1.0))
    }
}

func emptyRegistry() -> Registry {
    return Registry()
}
