import Foundation

enum Status {
    case active
    case frozen

    func label() -> String {
        return "status"
    }
}

extension String {
    func shout() -> String {
        return self.uppercased()
    }
}

struct Holder<T> {
    var value: T

    func unwrap() -> T {
        return value
    }
}

func combine<T>(_ left: T, _ right: T) -> [T] {
    return [left, right]
}

protocol Renderable {
    associatedtype Output
    func render() -> Output
}
