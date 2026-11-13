//! Text rules of the guard: name and path validators, one-line messages,
//! shell quoting. Values are raw bytes, as the environment holds them; where
//! the Python guard works on characters it decoded UTF-8 with
//! surrogateescape, so an invalid byte counts as one character here too.
const std = @import("std");

const Allocator = std.mem.Allocator;

pub const max_module_name = 256;
pub const prolog_line_max = 4094;

fn isAlnumU(c: u8) bool {
    return std.ascii.isAlphanumeric(c) or c == '_';
}

/// ENV_NAME_RE: ^[A-Za-z_][A-Za-z0-9_]*$
pub fn envName(s: []const u8) bool {
    if (s.len == 0 or !(std.ascii.isAlphabetic(s[0]) or s[0] == '_')) return false;
    for (s[1..]) |c| if (!isAlnumU(c)) return false;
    return true;
}

/// _MOD_SEGMENT: [A-Za-z0-9_][A-Za-z0-9._+-]*
fn modSegment(s: []const u8) bool {
    if (s.len == 0 or !isAlnumU(s[0])) return false;
    for (s[1..]) |c| if (!(isAlnumU(c) or c == '.' or c == '+' or c == '-')) return false;
    return true;
}

/// MODULE_NAME_RE (two or more segments) or YEAR_MODULE_RE (four digits),
/// at most 256 bytes. The Python pattern is ASCII-only, so bytes and
/// characters agree.
pub fn moduleName(s: []const u8) bool {
    if (s.len == 0 or s.len > max_module_name) return false;
    if (s.len == 4 and allDigits(s)) return true;
    var it = std.mem.splitScalar(u8, s, '/');
    var n: usize = 0;
    while (it.next()) |seg| : (n += 1) if (!modSegment(seg)) return false;
    return n >= 2;
}

fn allDigits(s: []const u8) bool {
    for (s) |c| if (!std.ascii.isDigit(c)) return false;
    return s.len > 0;
}

/// EESSI_MODULE_RE: ^EESSI/<segment>$
pub fn eessiModule(s: []const u8) bool {
    return std.mem.startsWith(u8, s, "EESSI/") and modSegment(s["EESSI/".len..]);
}

/// ACCEL_RE: ^accel/(?:nvidia/cc[0-9]+|amd/gfx[0-9a-f]+)$
pub fn accelSubdir(s: []const u8) bool {
    if (std.mem.startsWith(u8, s, "accel/nvidia/cc")) return allDigits(s["accel/nvidia/cc".len..]);
    if (std.mem.startsWith(u8, s, "accel/amd/gfx")) {
        const rest = s["accel/amd/gfx".len..];
        if (rest.len == 0) return false;
        for (rest) |c| if (!(std.ascii.isDigit(c) or (c >= 'a' and c <= 'f'))) return false;
        return true;
    }
    return false;
}

/// safe_rel_subdir: relative, no empty, '.', '..' or backslash; each
/// segment [A-Za-z0-9_.+-]+.
pub fn safeRelSubdir(s: []const u8) bool {
    if (s.len == 0 or s[0] == '/' or std.mem.indexOfScalar(u8, s, '\\') != null) return false;
    var it = std.mem.splitScalar(u8, s, '/');
    while (it.next()) |seg| {
        if (seg.len == 0 or std.mem.eql(u8, seg, ".") or std.mem.eql(u8, seg, "..")) return false;
        for (seg) |c| if (!(isAlnumU(c) or c == '.' or c == '+' or c == '-')) return false;
    }
    return true;
}

/// safe_absolute: starts with '/', no newline, carriage return or NUL, no
/// '..' segment.
pub fn safeAbsolute(s: []const u8) bool {
    if (s.len == 0 or s[0] != '/' or hasLineBreakOrNul(s)) return false;
    var it = std.mem.splitScalar(u8, s, '/');
    while (it.next()) |seg| if (std.mem.eql(u8, seg, "..")) return false;
    return true;
}

pub fn hasLineBreakOrNul(s: []const u8) bool {
    return std.mem.indexOfAny(u8, s, "\n\r\x00") != null;
}

/// _SAFE_COMPONENT_RE: ^[A-Za-z0-9._-]{1,64}$
pub fn safeComponent(s: []const u8) bool {
    if (s.len == 0 or s.len > 64) return false;
    for (s) |c| if (!(isAlnumU(c) or c == '.' or c == '-')) return false;
    return true;
}

pub fn valueEmitOk(v: []const u8, multiline: bool) bool {
    if (std.mem.indexOfScalar(u8, v, 0) != null) return false;
    if (!multiline and std.mem.indexOfAny(u8, v, "\n\r") != null) return false;
    return true;
}

const never_emit = [_][]const u8{
    "_",             "SHLVL",        "PWD",       "OLDPWD",        "TMPDIR",
    "BASH_ENV",      "ENV",          "SHELLOPTS", "BASHOPTS",      "BASH_XTRACEFD",
    "PS4",           "BASH_ARGC",    "BASH_ARGV", "BASH_LINENO",   "BASH_SOURCE",
    "BASH_VERSINFO", "BASH_VERSION", "DIRSTACK",  "EPOCHREALTIME", "EPOCHSECONDS",
    "FUNCNAME",      "GROUPS",       "HOSTNAME",  "HOSTTYPE",      "IFS",
    "MACHTYPE",      "OPTERR",       "OPTIND",    "OSTYPE",        "PIPESTATUS",
    "PPID",
};

pub const soft_drop = [_][]const u8{ "PS1", "PS2", "PS3", "PROMPT_COMMAND" };
pub const startup_vars = [_][]const u8{ "BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "BASH_XTRACEFD", "PS4" };

pub fn inList(list: []const []const u8, s: []const u8) bool {
    for (list) |x| if (std.mem.eql(u8, x, s)) return true;
    return false;
}

/// _never_touch: NEVER_EMIT, BASH_FUNC_*, SLURM_*.
pub fn neverTouch(name: []const u8) bool {
    return inList(&never_emit, name) or std.mem.startsWith(u8, name, "BASH_FUNC_") or std.mem.startsWith(u8, name, "SLURM_");
}

/// _never_cache: the never-touch list and the loader's LD_*.
pub fn neverCache(name: []const u8) bool {
    return neverTouch(name) or std.mem.startsWith(u8, name, "LD_");
}

// ---- characters -------------------------------------------------------

pub const Char = struct {
    /// The code point, or null for a byte that is not part of valid UTF-8.
    cp: ?u21,
    len: u3,
};

/// The next character of `s` as CPython's UTF-8 decoder sees it: a valid
/// sequence, or an invalid byte run (the maximal subpart of a sequence,
/// returned one byte at a time, as surrogateescape replaces it).
pub fn nextChar(s: []const u8) Char {
    const c0 = s[0];
    if (c0 < 0x80) return .{ .cp = c0, .len = 1 };
    const n: u3 = std.unicode.utf8ByteSequenceLength(c0) catch return .{ .cp = null, .len = 1 };
    if (s.len < n) return .{ .cp = null, .len = 1 };
    const cp = std.unicode.utf8Decode(s[0..n]) catch return .{ .cp = null, .len = 1 };
    return .{ .cp = cp, .len = n };
}

/// Length of the maximal subpart of an ill-formed sequence at `s` (Unicode
/// "best practice", which CPython's 'replace' handler follows): the bytes
/// that one U+FFFD stands for.
fn maximalSubpart(s: []const u8) usize {
    const c0 = s[0];
    var need: usize = 0;
    var lo: u8 = 0x80;
    var hi: u8 = 0xBF;
    switch (c0) {
        0xC2...0xDF => need = 1,
        0xE0 => {
            need = 2;
            lo = 0xA0;
        },
        0xE1...0xEC, 0xEE...0xEF => need = 2,
        0xED => {
            need = 2;
            hi = 0x9F;
        },
        0xF0 => {
            need = 3;
            lo = 0x90;
        },
        0xF1...0xF3 => need = 3,
        0xF4 => {
            need = 3;
            hi = 0x8F;
        },
        else => return 1,
    }
    var i: usize = 1;
    while (i <= need and i < s.len) : (i += 1) {
        const b = s[i];
        const l: u8 = if (i == 1) lo else 0x80;
        const h: u8 = if (i == 1) hi else 0xBF;
        if (b < l or b > h) break;
    }
    return i;
}

/// bytes.decode("utf-8", "replace"): each maximal ill-formed subpart becomes
/// one U+FFFD.
pub fn decodeReplace(gpa: Allocator, s: []const u8) Allocator.Error![]u8 {
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(gpa);
    var i: usize = 0;
    while (i < s.len) {
        const ch = nextChar(s[i..]);
        if (ch.cp != null) {
            try out.appendSlice(gpa, s[i .. i + ch.len]);
            i += ch.len;
        } else {
            try out.appendSlice(gpa, "\u{FFFD}");
            i += maximalSubpart(s[i..]);
        }
    }
    return out.toOwnedSlice(gpa);
}

/// str.isspace for the characters str.strip() removes.
pub fn pyWhitespace(cp: u21) bool {
    return switch (cp) {
        ' ', '\t', '\n', 0x0b, 0x0c, '\r', 0x1c...0x1f, 0x85, 0xa0, 0x1680, 0x2000...0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000 => true,
        else => false,
    };
}

/// str.strip(): `s` without leading and trailing Unicode whitespace.
pub fn pyStrip(s: []const u8) []const u8 {
    var a: usize = 0;
    while (a < s.len) {
        const ch = nextChar(s[a..]);
        const cp = ch.cp orelse break;
        if (!pyWhitespace(cp)) break;
        a += ch.len;
    }
    var b: usize = a;
    var i: usize = a;
    while (i < s.len) {
        const ch = nextChar(s[i..]);
        i += ch.len;
        const ws = if (ch.cp) |cp| pyWhitespace(cp) else false;
        if (!ws) b = i;
    }
    return s[a..b];
}

/// str.split() with no separator: the runs between Unicode whitespace.
pub fn pySplit(arena: Allocator, s: []const u8) Allocator.Error![]const []const u8 {
    var out: std.ArrayList([]const u8) = .empty;
    var i: usize = 0;
    var start: ?usize = null;
    while (i < s.len) {
        const ch = nextChar(s[i..]);
        const ws = if (ch.cp) |cp| pyWhitespace(cp) else false;
        if (ws) {
            if (start) |st| try out.append(arena, s[st..i]);
            start = null;
        } else if (start == null) start = i;
        i += ch.len;
    }
    if (start) |st| try out.append(arena, s[st..]);
    return out.items;
}

/// one_line: controls (below 32 and 127) become spaces, the result is
/// stripped, and more than `limit` characters are cut to limit-3 plus "...".
pub fn oneLine(gpa: Allocator, s: []const u8, limit: usize) Allocator.Error![]u8 {
    var chars: std.ArrayList(struct { start: usize, len: usize, space: bool }) = .empty;
    defer chars.deinit(gpa);
    var buf: std.ArrayList(u8) = .empty;
    errdefer buf.deinit(gpa);
    var i: usize = 0;
    while (i < s.len) {
        const ch = nextChar(s[i..]);
        const start = buf.items.len;
        var space = false;
        if (ch.cp) |cp| {
            if (cp < 32 or cp == 127) {
                try buf.append(gpa, ' ');
                space = true;
            } else {
                try buf.appendSlice(gpa, s[i .. i + ch.len]);
                space = pyWhitespace(cp);
            }
        } else try buf.append(gpa, s[i]);
        try chars.append(gpa, .{ .start = start, .len = buf.items.len - start, .space = space });
        i += ch.len;
    }
    var a: usize = 0;
    var b: usize = chars.items.len;
    while (a < b and chars.items[a].space) a += 1;
    while (b > a and chars.items[b - 1].space) b -= 1;
    const kept = chars.items[a..b];
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(gpa);
    if (kept.len > limit) {
        const n = if (limit >= 3) limit - 3 else 0;
        for (kept[0..n]) |c| try out.appendSlice(gpa, buf.items[c.start .. c.start + c.len]);
        try out.appendSlice(gpa, "...");
    } else {
        for (kept) |c| try out.appendSlice(gpa, buf.items[c.start .. c.start + c.len]);
    }
    buf.deinit(gpa);
    return out.toOwnedSlice(gpa);
}

/// message_bytes: one_line(text, 500), encoded as UTF-8 with an invalid
/// byte written as '?', cut at a character boundary to fit after "print "
/// in a 4094-byte prolog line.
pub fn messageBytes(gpa: Allocator, text: []const u8) Allocator.Error![]u8 {
    const line = try oneLine(gpa, text, 500);
    defer gpa.free(line);
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(gpa);
    const room = prolog_line_max - "print ".len - 1;
    var i: usize = 0;
    while (i < line.len) {
        const ch = nextChar(line[i..]);
        const len: usize = if (ch.cp == null) 1 else ch.len;
        if (out.items.len + len > room) break;
        if (ch.cp == null) try out.append(gpa, '?') else try out.appendSlice(gpa, line[i .. i + ch.len]);
        i += ch.len;
    }
    return out.toOwnedSlice(gpa);
}

/// shlex.quote: unchanged when every byte is in [A-Za-z0-9_@%+=:,./-] and
/// the value is not empty; otherwise single-quoted with ' written as '"'"'.
pub fn shellQuote(gpa: Allocator, v: []const u8) Allocator.Error![]u8 {
    if (v.len == 0) return gpa.dupe(u8, "''");
    const safe = for (v) |c| {
        if (!(isAlnumU(c) or std.mem.indexOfScalar(u8, "@%+=:,./-", c) != null)) break false;
    } else true;
    if (safe) return gpa.dupe(u8, v);
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(gpa);
    try out.append(gpa, '\'');
    for (v) |c| {
        if (c == '\'') try out.appendSlice(gpa, "'\"'\"'") else try out.append(gpa, c);
    }
    try out.append(gpa, '\'');
    return out.toOwnedSlice(gpa);
}

test "validators" {
    try std.testing.expect(moduleName("GCC/13.2.0"));
    try std.testing.expect(moduleName("2025"));
    try std.testing.expect(!moduleName("GCC"));
    try std.testing.expect(!moduleName("GCC//1"));
    try std.testing.expect(!moduleName("-x/1"));
    try std.testing.expect(eessiModule("EESSI/2023.06"));
    try std.testing.expect(!eessiModule("EESSI/2023/06"));
    try std.testing.expect(accelSubdir("accel/nvidia/cc90"));
    try std.testing.expect(accelSubdir("accel/amd/gfx90a"));
    try std.testing.expect(!accelSubdir("accel/amd/gfx90A"));
    try std.testing.expect(!accelSubdir("accel/nvidia/cc"));
    try std.testing.expect(safeRelSubdir("x86_64/amd/zen4"));
    try std.testing.expect(!safeRelSubdir("x86_64//zen4"));
    try std.testing.expect(!safeRelSubdir("../x"));
    try std.testing.expect(safeAbsolute("/cvmfs/x"));
    try std.testing.expect(!safeAbsolute("/a/../b"));
    try std.testing.expect(safeComponent("int4.local"));
    try std.testing.expect(!safeComponent("a b"));
    try std.testing.expect(neverCache("LD_PRELOAD") and neverTouch("SLURM_JOB_ID") and !neverTouch("LD_PRELOAD"));
}

test "oneLine and messageBytes" {
    const gpa = std.testing.allocator;
    const a = try oneLine(gpa, "  a\nb\x7f\x00c  ", 500);
    defer gpa.free(a);
    try std.testing.expectEqualStrings("a b  c", a);
    const b = try oneLine(gpa, "abcdefgh", 5);
    defer gpa.free(b);
    try std.testing.expectEqualStrings("ab...", b);
    const c = try oneLine(gpa, "\u{00a0}x\xff\u{3000}", 500);
    defer gpa.free(c);
    try std.testing.expectEqualStrings("x\xff", c);
    const m = try messageBytes(gpa, "x\xffy\u{e9}");
    defer gpa.free(m);
    try std.testing.expectEqualStrings("x?y\u{e9}", m);
}

test "decodeReplace uses maximal subparts" {
    const gpa = std.testing.allocator;
    const r = try decodeReplace(gpa, "a\xe2\x82b\xff\xf0\x9f\x98");
    defer gpa.free(r);
    try std.testing.expectEqualStrings("a\u{FFFD}b\u{FFFD}\u{FFFD}", r);
}

test "pyStrip and pySplit use Unicode whitespace" {
    try std.testing.expectEqualStrings("a b", pyStrip("\u{3000} a b\u{a0}\n"));
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const parts = try pySplit(arena_state.allocator(), "avx\u{a0}waitpkg  x\xff");
    try std.testing.expectEqual(@as(usize, 3), parts.len);
    try std.testing.expectEqualStrings("waitpkg", parts[1]);
}

test "shellQuote" {
    const gpa = std.testing.allocator;
    const cases = [_][2][]const u8{
        .{ "", "''" },
        .{ "/a:b", "/a:b" },
        .{ "a b", "'a b'" },
        .{ "it's", "'it'\"'\"'s'" },
        .{ "\xe9", "'\xe9'" },
    };
    for (cases) |cs| {
        const q = try shellQuote(gpa, cs[0]);
        defer gpa.free(q);
        try std.testing.expectEqualStrings(cs[1], q);
    }
}
