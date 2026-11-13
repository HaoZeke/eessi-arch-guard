//! The JSON the guard reads and writes, with the two string encodings the
//! Python guard used:
//! - cache: json.dumps(ensure_ascii=True) of text decoded with
//!   surrogateescape, so a byte that is not UTF-8 is a lone \udcXX;
//! - wire: the restore helper's request and reply, one character per byte
//!   (latin-1), so every byte is \u00XX or itself.
//! Strings are kept as their raw JSON text until a codec decodes them.
const std = @import("std");
const text = @import("text.zig");

const Allocator = std.mem.Allocator;

pub const Value = union(enum) {
    null,
    bool: bool,
    /// The number's JSON text.
    number: []const u8,
    /// The string's JSON text between the quotes, escapes not yet decoded.
    string: []const u8,
    array: []const Value,
    /// Members in order; a later duplicate key wins, as json.loads keeps it.
    object: []const Member,

    pub fn get(v: Value, key: []const u8) ?Value {
        if (v != .object) return null;
        var found: ?Value = null;
        for (v.object) |m| {
            if (keyEql(m.key, key)) found = m.value;
        }
        return found;
    }
};

pub const Member = struct { key: []const u8, value: Value };

/// A raw JSON key equals `want` (ASCII, as every key the guard looks up
/// is) once its escapes are decoded.
fn keyEql(raw: []const u8, want: []const u8) bool {
    var i: usize = 0;
    var j: usize = 0;
    while (i < raw.len) {
        var c: u21 = raw[i];
        if (c == '\\') {
            const e = raw[i + 1];
            if (e == 'u') {
                c = std.fmt.parseInt(u16, raw[i + 2 .. i + 6], 16) catch return false;
                i += 6;
            } else {
                c = switch (e) {
                    'b' => 0x08,
                    'f' => 0x0c,
                    'n' => '\n',
                    'r' => '\r',
                    't' => '\t',
                    else => e,
                };
                i += 2;
            }
        } else i += 1;
        if (j >= want.len or c != want[j]) return false;
        j += 1;
    }
    return j == want.len;
}

fn isHex(s: []const u8) bool {
    if (s.len != 4) return false;
    for (s) |c| if (!std.ascii.isHex(c)) return false;
    return true;
}

pub const ParseError = Allocator.Error || error{Syntax};

const max_depth = 32;

const Parser = struct {
    s: []const u8,
    i: usize = 0,
    arena: Allocator,

    fn ws(p: *Parser) void {
        while (p.i < p.s.len and (p.s[p.i] == ' ' or p.s[p.i] == '\t' or p.s[p.i] == '\n' or p.s[p.i] == '\r')) p.i += 1;
    }

    fn lit(p: *Parser, l: []const u8) bool {
        if (std.mem.startsWith(u8, p.s[p.i..], l)) {
            p.i += l.len;
            return true;
        }
        return false;
    }

    fn value(p: *Parser, depth: usize) ParseError!Value {
        if (depth > max_depth) return error.Syntax;
        p.ws();
        if (p.i >= p.s.len) return error.Syntax;
        switch (p.s[p.i]) {
            'n' => return if (p.lit("null")) .null else error.Syntax,
            't' => return if (p.lit("true")) .{ .bool = true } else error.Syntax,
            'f' => return if (p.lit("false")) .{ .bool = false } else error.Syntax,
            '"' => return .{ .string = try p.string() },
            '[' => {
                p.i += 1;
                var items: std.ArrayList(Value) = .empty;
                p.ws();
                if (p.i < p.s.len and p.s[p.i] == ']') {
                    p.i += 1;
                    return .{ .array = &.{} };
                }
                while (true) {
                    try items.append(p.arena, try p.value(depth + 1));
                    p.ws();
                    if (p.i >= p.s.len) return error.Syntax;
                    if (p.s[p.i] == ',') {
                        p.i += 1;
                        continue;
                    }
                    if (p.s[p.i] == ']') {
                        p.i += 1;
                        return .{ .array = items.items };
                    }
                    return error.Syntax;
                }
            },
            '{' => {
                p.i += 1;
                var members: std.ArrayList(Member) = .empty;
                p.ws();
                if (p.i < p.s.len and p.s[p.i] == '}') {
                    p.i += 1;
                    return .{ .object = &.{} };
                }
                while (true) {
                    p.ws();
                    if (p.i >= p.s.len or p.s[p.i] != '"') return error.Syntax;
                    const key = try p.string();
                    p.ws();
                    if (p.i >= p.s.len or p.s[p.i] != ':') return error.Syntax;
                    p.i += 1;
                    const v = try p.value(depth + 1);
                    try members.append(p.arena, .{ .key = key, .value = v });
                    p.ws();
                    if (p.i >= p.s.len) return error.Syntax;
                    if (p.s[p.i] == ',') {
                        p.i += 1;
                        continue;
                    }
                    if (p.s[p.i] == '}') {
                        p.i += 1;
                        return .{ .object = members.items };
                    }
                    return error.Syntax;
                }
            },
            '-', '0'...'9' => {
                const start = p.i;
                if (p.s[p.i] == '-') p.i += 1;
                const digits = p.i;
                while (p.i < p.s.len and std.ascii.isDigit(p.s[p.i])) p.i += 1;
                if (p.i == digits) return error.Syntax;
                // JSON has no leading zeros: 0 alone, or 1-9 then digits.
                if (p.s[digits] == '0' and p.i - digits > 1) return error.Syntax;
                if (p.i < p.s.len and p.s[p.i] == '.') {
                    p.i += 1;
                    const f = p.i;
                    while (p.i < p.s.len and std.ascii.isDigit(p.s[p.i])) p.i += 1;
                    if (p.i == f) return error.Syntax;
                }
                if (p.i < p.s.len and (p.s[p.i] == 'e' or p.s[p.i] == 'E')) {
                    p.i += 1;
                    if (p.i < p.s.len and (p.s[p.i] == '+' or p.s[p.i] == '-')) p.i += 1;
                    const e = p.i;
                    while (p.i < p.s.len and std.ascii.isDigit(p.s[p.i])) p.i += 1;
                    if (p.i == e) return error.Syntax;
                }
                return .{ .number = p.s[start..p.i] };
            },
            else => return error.Syntax,
        }
    }

    /// The raw text of a string; escapes are checked for shape here.
    fn string(p: *Parser) ParseError![]const u8 {
        p.i += 1;
        const start = p.i;
        while (p.i < p.s.len) {
            const c = p.s[p.i];
            if (c == '"') {
                const raw = p.s[start..p.i];
                p.i += 1;
                return raw;
            }
            if (c < 0x20) return error.Syntax;
            if (c == '\\') {
                if (p.i + 1 >= p.s.len) return error.Syntax;
                switch (p.s[p.i + 1]) {
                    '"', '\\', '/', 'b', 'f', 'n', 'r', 't' => p.i += 2,
                    'u' => {
                        if (p.i + 6 > p.s.len) return error.Syntax;
                        if (!isHex(p.s[p.i + 2 .. p.i + 6])) return error.Syntax;
                        p.i += 6;
                    },
                    else => return error.Syntax,
                }
                continue;
            }
            p.i += 1;
        }
        return error.Syntax;
    }
};

/// Parse one JSON document; all memory comes from `arena`.
pub fn parse(arena: Allocator, s: []const u8) ParseError!Value {
    var p: Parser = .{ .s = s, .arena = arena };
    const v = try p.value(0);
    p.ws();
    if (p.i != s.len) return error.Syntax;
    return v;
}

pub const DecodeError = Allocator.Error || error{Encoding};

/// Code points of a raw JSON string, surrogate pairs joined; a lone
/// surrogate is returned as itself.
fn codePoints(arena: Allocator, raw: []const u8) DecodeError![]u21 {
    var out: std.ArrayList(u21) = .empty;
    var i: usize = 0;
    while (i < raw.len) {
        const c = raw[i];
        if (c != '\\') {
            const ch = text.nextChar(raw[i..]);
            const cp = ch.cp orelse return error.Encoding; // json.loads takes text
            try out.append(arena, cp);
            i += ch.len;
            continue;
        }
        const e = raw[i + 1];
        i += 2;
        const simple: ?u21 = switch (e) {
            '"' => '"',
            '\\' => '\\',
            '/' => '/',
            'b' => 0x08,
            'f' => 0x0c,
            'n' => '\n',
            'r' => '\r',
            't' => '\t',
            else => null,
        };
        if (simple) |s| {
            try out.append(arena, s);
            continue;
        }
        var cp: u21 = std.fmt.parseInt(u16, raw[i .. i + 4], 16) catch return error.Encoding;
        i += 4;
        if (cp >= 0xD800 and cp <= 0xDBFF and i + 6 <= raw.len and raw[i] == '\\' and raw[i + 1] == 'u') {
            const lo = std.fmt.parseInt(u16, raw[i + 2 .. i + 6], 16) catch return error.Encoding;
            if (lo >= 0xDC00 and lo <= 0xDFFF) {
                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                i += 6;
            }
        }
        try out.append(arena, cp);
    }
    return out.items;
}

/// Cache text: code points as UTF-8, a lone surrogate U+DC80..U+DCFF as the
/// byte it escapes; any other lone surrogate cannot be written to the
/// environment and is an error.
pub fn decodeCache(arena: Allocator, raw: []const u8) DecodeError![]u8 {
    const cps = try codePoints(arena, raw);
    var out: std.ArrayList(u8) = .empty;
    for (cps) |cp| {
        if (cp >= 0xDC80 and cp <= 0xDCFF) {
            try out.append(arena, @intCast(cp - 0xDC00));
        } else if (cp >= 0xD800 and cp <= 0xDFFF) {
            return error.Encoding;
        } else {
            var b: [4]u8 = undefined;
            const n = std.unicode.utf8Encode(cp, &b) catch return error.Encoding;
            try out.appendSlice(arena, b[0..n]);
        }
    }
    return out.items;
}

/// Wire text: every code point must be at most U+00FF and is one byte.
pub fn decodeWire(arena: Allocator, raw: []const u8) DecodeError![]u8 {
    const cps = try codePoints(arena, raw);
    const out = try arena.alloc(u8, cps.len);
    for (cps, 0..) |cp, i| {
        if (cp > 0xFF) return error.Encoding;
        out[i] = @intCast(cp);
    }
    return out;
}

fn escapeCp(out: *std.ArrayList(u8), gpa: Allocator, cp: u21) Allocator.Error!void {
    switch (cp) {
        '"' => try out.appendSlice(gpa, "\\\""),
        '\\' => try out.appendSlice(gpa, "\\\\"),
        '\n' => try out.appendSlice(gpa, "\\n"),
        '\r' => try out.appendSlice(gpa, "\\r"),
        '\t' => try out.appendSlice(gpa, "\\t"),
        0x08 => try out.appendSlice(gpa, "\\b"),
        0x0c => try out.appendSlice(gpa, "\\f"),
        ' '...'!', '#'...'[', ']'...'~' => try out.append(gpa, @intCast(cp)),
        else => {
            var b: [12]u8 = undefined;
            if (cp >= 0x10000) {
                const v = cp - 0x10000;
                const s = std.fmt.bufPrint(&b, "\\u{x:0>4}\\u{x:0>4}", .{ 0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF) }) catch unreachable;
                try out.appendSlice(gpa, s);
            } else {
                const s = std.fmt.bufPrint(&b, "\\u{x:0>4}", .{cp}) catch unreachable;
                try out.appendSlice(gpa, s);
            }
        },
    }
}

/// A JSON string of `bytes` as the Python guard wrote cache text.
pub fn writeCacheString(out: *std.ArrayList(u8), gpa: Allocator, bytes: []const u8) Allocator.Error!void {
    try out.append(gpa, '"');
    var i: usize = 0;
    while (i < bytes.len) {
        const ch = text.nextChar(bytes[i..]);
        if (ch.cp) |cp| try escapeCp(out, gpa, cp) else try escapeCp(out, gpa, 0xDC00 + @as(u21, bytes[i]));
        i += ch.len;
    }
    try out.append(gpa, '"');
}

/// A JSON string of `bytes` as wire text: one code point per byte.
pub fn writeWireString(out: *std.ArrayList(u8), gpa: Allocator, bytes: []const u8) Allocator.Error!void {
    try out.append(gpa, '"');
    for (bytes) |b| try escapeCp(out, gpa, b);
    try out.append(gpa, '"');
}

test "cache strings round-trip bytes that are not UTF-8" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const a = arena_state.allocator();
    var out: std.ArrayList(u8) = .empty;
    try writeCacheString(&out, a, "a\"\xff\u{e9}\u{1F600}\n\x7f");
    try std.testing.expectEqualStrings("\"a\\\"\\udcff\\u00e9\\ud83d\\ude00\\n\\u007f\"", out.items);
    const v = try parse(a, out.items);
    try std.testing.expectEqualStrings("a\"\xff\u{e9}\u{1F600}\n\x7f", try decodeCache(a, v.string));
    try std.testing.expectError(error.Encoding, decodeCache(a, "\\ud800"));
}

test "wire strings are one byte per code point" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const a = arena_state.allocator();
    var out: std.ArrayList(u8) = .empty;
    try writeWireString(&out, a, "x\xff\xc3\xa9");
    try std.testing.expectEqualStrings("\"x\\u00ff\\u00c3\\u00a9\"", out.items);
    const v = try parse(a, out.items);
    try std.testing.expectEqualStrings("x\xff\xc3\xa9", try decodeWire(a, v.string));
    try std.testing.expectError(error.Encoding, decodeWire(a, "\\u0100"));
}

test "parse objects, duplicates and nesting" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const a = arena_state.allocator();
    const v = try parse(a, " {\"v\": 1, \"k\": [true, null, -1.5e3], \"v\": 2} ");
    try std.testing.expectEqualStrings("2", v.get("v").?.number);
    try std.testing.expectEqual(@as(usize, 3), v.get("k").?.array.len);
    try std.testing.expectError(error.Syntax, parse(a, "{\"a\":1,}"));
    try std.testing.expectError(error.Syntax, parse(a, "{\"v\":01}"));
    try std.testing.expectError(error.Syntax, parse(a, "\"\\u+123\""));
    const e = try parse(a, "{\"\\u006bind\": \"x\"}");
    try std.testing.expect(e.get("kind") != null);
    try std.testing.expectError(error.Syntax, parse(a, "[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]"));
}
