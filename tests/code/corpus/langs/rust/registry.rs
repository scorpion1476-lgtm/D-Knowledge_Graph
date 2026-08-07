use crate::shapes::make_circle;

pub struct Registry {
    pub count: usize,
}

impl Registry {
    pub fn seed(&mut self) {
        make_circle(1.0);
        self.count = 1;
    }
}

pub fn empty_registry() -> Registry {
    Registry { count: 0 }
}
