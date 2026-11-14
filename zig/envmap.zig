//! An environment as a map of raw byte names to raw byte values, the delta
//! between two of them, and the prolog or shell text that applies a delta.
const std = @import("std");
const common = @import("zig_common");
const text = @import("text.zig");

const Allocator = std.mem.Allocator;

/// name -> value; every name once. Owned by the arena it was built in.
pub const Map = std.StringArrayHashMapUnmanaged([]const u8);

/// name -> new value, or null to unset.
pub const Changes = std.StringArrayHashMapUnmanaged(?[]const u8);

/// The environment block, first entry of a name winning, as getenv(3) and
/// Python's os.environ read it.
pub fn fromEnvp(arena: Allocator, envp: common.proc.Envp) Allocator.Error!Map {
    var m: Map = .empty;
    var i: usize = 0;
    while (envp[i]) |p| : (i += 1) {
        const entry = std.mem.span(p);
        const eq = std.mem.findScalar(u8, entry, '=') orelse continue;
        const r = try m.getOrPut(arena, entry[0..eq]);
        if (!r.found_existing) r.value_ptr.* = entry[eq + 1 ..];
    }
    return m;
}

pub fn clone(arena: Allocator, m: Map) Allocator.Error!Map {
    return m.clone(arena);
}

/// A NULL-terminated block for execve.
pub fn toEnvp(arena: Allocator, m: Map) (Allocator.Error || error{InvalidArgument})!common.proc.Envp {
    const block = try arena.allocSentinel(?[*:0]const u8, m.count(), null);
    var it = m.iterator();
    var i: usize = 0;
    while (it.next()) |kv| : (i += 1) {
        const k = kv.key_ptr.*;
        const v = kv.value_ptr.*;
        if (std.mem.findScalar(u8, k, 0) != null or std.mem.findScalar(u8, v, 0) != null) return error.InvalidArgument;
        const e = try arena.allocSentinel(u8, k.len + 1 + v.len, 0);
        @memcpy(e[0..k.len], k);
        e[k.len] = '=';
        @memcpy(e[k.len + 1 ..], v);
        block[i] = e.ptr;
    }
    return block.ptr;
}

/// strip_startup: the map without the variables that make bash read files
/// or trace.
pub fn stripStartup(arena: Allocator, m: Map) Allocator.Error!Map {
    var c = try m.clone(arena);
    for (text.startup_vars) |k| _ = c.orderedRemove(k);
    return c;
}

/// environment_delta: the changes to publish, or null when a value cannot
/// be carried in this output mode (nothing is published then).
pub fn delta(arena: Allocator, old: Map, new: Map, multiline: bool) Allocator.Error!?Changes {
    var changes: Changes = .empty;
    var unsafe = false;
    // set(old) | set(new)
    var names: std.StringArrayHashMapUnmanaged(void) = .empty;
    for (old.keys()) |k| try names.put(arena, k, {});
    for (new.keys()) |k| try names.put(arena, k, {});
    for (names.keys()) |key| {
        if (text.neverTouch(key)) continue;
        const old_v = old.get(key);
        const new_v = new.get(key);
        if (eqlOpt(old_v, new_v)) continue;
        if (!text.envName(key)) {
            if (!text.inList(&text.soft_drop, key)) unsafe = true;
            continue;
        }
        const nv = new_v orelse {
            try changes.put(arena, key, null);
            continue;
        };
        if (!text.valueEmitOk(nv, multiline)) {
            if (!text.inList(&text.soft_drop, key)) unsafe = true;
            continue;
        }
        try changes.put(arena, key, nv);
    }
    if (unsafe) return null;
    return changes;
}

fn eqlOpt(a: ?[]const u8, b: ?[]const u8) bool {
    if (a == null and b == null) return true;
    if (a == null or b == null) return false;
    return std.mem.eql(u8, a.?, b.?);
}

pub const RenderError = Allocator.Error || error{ UnsafeName, UnsafeValue, LineTooLong };

/// The ValueError text the Python guard raised for each refusal.
pub fn renderErrorText(err: RenderError) []const u8 {
    return switch (err) {
        error.UnsafeName => "refusing to emit an unsafe name",
        error.UnsafeValue => "refusing to emit an unsafe prolog value",
        error.LineTooLong => "refusing to emit a prolog line past the Slurm 17.11 read",
        error.OutOfMemory => "out of memory",
    };
}

fn lessThan(_: void, a: []const u8, b: []const u8) bool {
    return std.mem.lessThan(u8, a, b);
}

/// render_bytes: unset and export lines in name order, then in prolog mode
/// with `announce` a print line. Prolog values are written as they are and
/// each line must fit the Slurm 17.11 read; shell values are shell-quoted.
pub fn render(gpa: Allocator, changes: Changes, message: ?[]const u8, shell: bool, announce: bool) RenderError![]u8 {
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(gpa);
    const keys = try gpa.dupe([]const u8, changes.keys());
    defer gpa.free(keys);
    std.mem.sort([]const u8, keys, {}, lessThan);
    for (keys) |key| {
        if (!text.envName(key)) return error.UnsafeName;
        const value = changes.get(key).? orelse {
            try out.appendSlice(gpa, "unset ");
            try out.appendSlice(gpa, key);
            try out.append(gpa, '\n');
            continue;
        };
        if (shell) {
            const q = try text.shellQuote(gpa, value);
            defer gpa.free(q);
            try out.appendSlice(gpa, "export ");
            try out.appendSlice(gpa, key);
            try out.append(gpa, '=');
            try out.appendSlice(gpa, q);
            try out.append(gpa, '\n');
        } else {
            if (!text.valueEmitOk(value, false)) return error.UnsafeValue;
            const len = "export ".len + key.len + 1 + value.len + 1;
            if (len > text.prolog_line_max) return error.LineTooLong;
            try out.appendSlice(gpa, "export ");
            try out.appendSlice(gpa, key);
            try out.append(gpa, '=');
            try out.appendSlice(gpa, value);
            try out.append(gpa, '\n');
        }
    }
    if (message) |m| if (m.len > 0 and announce and !shell) {
        const mb = try text.messageBytes(gpa, m);
        defer gpa.free(mb);
        if ("print ".len + mb.len + 1 > text.prolog_line_max) return error.LineTooLong;
        try out.appendSlice(gpa, "print ");
        try out.appendSlice(gpa, mb);
        try out.append(gpa, '\n');
    };
    return out.toOwnedSlice(gpa);
}

test "delta and render" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const a = arena_state.allocator();
    var old: Map = .empty;
    try old.put(a, "PATH", "/old");
    try old.put(a, "GONE", "x");
    try old.put(a, "SLURM_X", "1");
    try old.put(a, "SAME", "s");
    var new: Map = .empty;
    try new.put(a, "PATH", "/new");
    try new.put(a, "SLURM_X", "2");
    try new.put(a, "SAME", "s");
    try new.put(a, "PS1", "a\nb");
    try new.put(a, "Q", "it's");
    const d = (try delta(a, old, new, false)).?;
    try std.testing.expectEqual(@as(usize, 3), d.count());
    const p = try render(a, d, "hello\nworld", false, true);
    try std.testing.expectEqualStrings("unset GONE\nexport PATH=/new\nexport Q=it's\nprint hello world\n", p);
    const s = try render(a, d, "hello", true, false);
    try std.testing.expectEqualStrings("unset GONE\nexport PATH=/new\nexport Q='it'\"'\"'s'\n", s);
    try new.put(a, "BAD", "a\nb");
    try std.testing.expect((try delta(a, old, new, false)) == null);
    try std.testing.expect((try delta(a, old, new, true)) != null);
}
