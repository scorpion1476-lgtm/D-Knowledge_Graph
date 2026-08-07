const shapes = @import("shapes.zig");

const Registry = struct {
    count: usize,

    pub fn seed(self: *Registry) void {
        self.count = 1;
    }
};

pub fn emptyRegistry() Registry {
    return Registry{ .count = 0 };
}

test "registry starts empty" {
    const r = emptyRegistry();
    try std.testing.expect(r.count == 0);
}
