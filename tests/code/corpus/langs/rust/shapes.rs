use std::f64::consts::PI;

pub struct Circle {
    pub radius: f64,
}

pub enum Kind {
    Round,
    Square,
}

pub trait Drawable {
    fn draw(&self);
}

impl Circle {
    pub fn area(&self) -> f64 {
        PI * self.radius * self.radius
    }

    pub fn describe(&self) -> String {
        format!("circle of area {}", self.area())
    }
}

pub fn make_circle(radius: f64) -> Circle {
    Circle { radius }
}

fn total_area(shapes: &[Circle]) -> f64 {
    shapes.iter().map(|shape| shape.area()).sum()
}
