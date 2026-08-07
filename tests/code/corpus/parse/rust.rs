struct Point {
    x: i64,
}

impl Point {
    fn norm(&self) -> i64 {
        self.x
    }
}

fn make_point() -> Point {
    Point { x: 1 }
}

fn rust_helper() -> i64 {
    7
}
