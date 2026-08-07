use std::fmt::Display;

pub mod inner {
    pub fn helper() -> u32 {
        1
    }
}

pub trait Render {
    fn render(&self) -> String;
    fn describe(&self) -> String {
        self.render()
    }
}

pub struct Widget;

impl Render for Widget {
    fn render(&self) -> String {
        String::from("widget")
    }
}

impl Widget {
    pub const LIMIT: u32 = 4;

    pub fn new() -> Self {
        Widget
    }
}

pub fn show<T: Display>(value: T) -> String {
    format!("{}", value)
}

pub async fn load() -> u32 {
    inner::helper()
}
