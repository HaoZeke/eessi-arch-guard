//! The decision: compare the loaded EESSI target with this node, and when
//! they differ reload the module environment through the restore helper
//! (this program run again with --restore) and return the delta. One
//! deadline covers the cache lock, both archdetect runs and the helper; a
//! SIGTERM during the decision kills the running child's process group.
const std = @import("std");
const linux = std.os.linux;
const common = @import("zig_common");
const text = @import("text.zig");
const envmap = @import("envmap.zig");
const json = @import("json.zig");

const sys = common.sys;
const fsx = common.fsx;
const proc = common.proc;
const state = common.state;
const Allocator = std.mem.Allocator;
const empty: []const u8 = "";
const Map = envmap.Map;
const Changes = envmap.Changes;

pub const diagnose_limit = 32;
const max_arch_stdout = 65536;
const cpuinfo_read_max = 16 * 1024 * 1024;
const cache_read_max = 8 * 1024 * 1024;
const lock_poll_ms = 50;

pub const key_fields = [_][]const u8{
    "EESSI_SOFTWARE_SUBDIR", "EESSI_ACCEL_SUBDIR", "LOADEDMODULES", "MODULEPATH",
    "EESSI_INIT_PREFIX",     "EESSI_PROC_CPUINFO", "PATH",
};

const waitpkg_marches = [_][]const u8{
    "alderlake",      "arrowlake",     "arrowlake_s",     "clearwaterforest", "diamondrapids",
    "grandridge",     "graniterapids", "graniterapids_d", "novalake",         "pantherlake",
    "sapphirerapids", "sierraforest",  "tremont",
};

pub const Kind = enum {
    skip,
    noop,
    reloaded,
    failed,
};

pub const Outcome = struct {
    changes: Changes = .empty,
    message: ?[]const u8 = null,
    kind: Kind,
    detail: ?[]const u8 = null,
};

/// The decision ran out of time, or SIGTERM arrived; neither is cached.
pub const Stop = error{ Deadline, Terminated };

// ---- the deadline and SIGTERM ------------------------------------------

pub const Limit = struct {
    deadline_ms: i64,
    terminated: std.atomic.Value(bool) = .init(false),
    committing: std.atomic.Value(bool) = .init(false),
    child_pgid: std.atomic.Value(i32) = .init(0),
    previous: linux.Sigaction = undefined,

    pub fn remainingMs(l: *const Limit) i64 {
        return l.deadline_ms - nowMs();
    }

    pub fn check(l: *const Limit) Stop!void {
        if (l.committing.load(.seq_cst)) return;
        if (l.terminated.load(.seq_cst)) return error.Terminated;
        if (l.remainingMs() <= 0) return error.Deadline;
    }
};

var active_limit: ?*Limit = null;

fn onTerm(_: linux.SIG) callconv(.c) void {
    const l = active_limit orelse return;
    l.terminated.store(true, .seq_cst);
    if (l.committing.load(.seq_cst)) return;
    const pgid = l.child_pgid.load(.seq_cst);
    if (pgid > 0) _ = linux.kill(-pgid, .KILL);
}

fn installLimit(l: *Limit) void {
    active_limit = l;
    var act: linux.Sigaction = .{ .handler = .{ .handler = onTerm }, .mask = linux.sigemptyset(), .flags = 0 };
    _ = linux.sigaction(.TERM, &act, &l.previous);
}

fn removeLimit(l: *Limit) void {
    _ = linux.sigaction(.TERM, &l.previous, null);
    active_limit = null;
}

pub fn nowMs() i64 {
    var ts: linux.timespec = undefined;
    _ = linux.clock_gettime(.MONOTONIC, &ts);
    return @as(i64, ts.sec) * 1000 + @divTrunc(@as(i64, ts.nsec), 1_000_000);
}

// ---- context -----------------------------------------------------------

pub const Ctx = struct {
    arena: Allocator,
    env: Map,
    strict: bool,
    /// Absolute path of this program, for the restore helper.
    self_exe: []const u8,
    limit: ?*Limit = null,
};

// ---- running programs ----------------------------------------------------

pub const RunError = Stop || error{ CannotRun, Unexpected };

pub const Ran = struct {
    code: u32,
    stdout: []const u8,
    stderr: []const u8,
};

/// The executable `name` would run as from an environment with `env`'s PATH
/// (os.defpath when it has none), as subprocess does; a name with '/' is a
/// path.
fn resolve(c: *Ctx, name: []const u8, env: Map) ?[]const u8 {
    if (std.mem.indexOfScalar(u8, name, '/') != null) {
        if (name[0] == '/') return name;
        var rb: [sys.path_max]u8 = undefined;
        const cwd = fsx.realpath(fsx.cwd, ".", &rb) catch return null;
        return std.fs.path.join(c.arena, &.{ cwd, name }) catch null;
    }
    const path = env.get("PATH") orelse "/bin:/usr/bin";
    return proc.which(c.arena, name, path) catch null;
}

/// The message subprocess raises for a program that cannot be found.
fn notFound(c: *Ctx, name: []const u8) []const u8 {
    return std.fmt.allocPrint(c.arena, "[Errno 2] No such file or directory: '{s}'", .{name}) catch "could not start";
}

/// run_captured: under the decision limit with `new_session`, the child's
/// session is killed at the deadline or on SIGTERM.
fn runCaptured(c: *Ctx, argv: []const []const u8, env: Map, input: ?[]const u8, new_session: bool, why: *[]const u8) RunError!Ran {
    const exe = resolve(c, argv[0], env) orelse {
        why.* = notFound(c, argv[0]);
        return error.CannotRun;
    };
    const envp = envmap.toEnvp(c.arena, env) catch {
        why.* = "embedded null byte";
        return error.CannotRun;
    };
    const limited = c.limit != null and new_session;
    if (limited) try c.limit.?.check();
    var opts: proc.Options = .{
        .stdin = input,
        .stdout_limit = 64 << 20,
        .stderr_limit = 1 << 20,
        .timeout_ms = null,
        .new_session = limited,
    };
    if (limited) {
        const l = c.limit.?;
        const left = l.remainingMs();
        if (left <= 0) return error.Deadline;
        opts.timeout_ms = @intCast(@min(left, std.math.maxInt(u32)));
        opts.grace_ms = 0;
        opts.cancel = &l.terminated;
        opts.pgid_out = &l.child_pgid;
    }
    const r = proc.run(c.arena, exe, argv, envp, opts) catch |err| switch (err) {
        error.TimedOut => return error.Deadline,
        error.Canceled => return error.Terminated,
        error.NotFound => {
            why.* = notFound(c, argv[0]);
            return error.CannotRun;
        },
        else => {
            why.* = @errorName(err);
            return error.CannotRun;
        },
    };
    if (limited) {
        const l = c.limit.?;
        if (l.terminated.load(.seq_cst) and !l.committing.load(.seq_cst)) return error.Terminated;
    }
    return .{ .code = r.term.code(), .stdout = r.stdout, .stderr = r.stderr };
}

fn runBash(c: *Ctx, args: []const []const u8, env: Map, why: *[]const u8) RunError!Ran {
    var argv: std.ArrayList([]const u8) = .empty;
    argv.appendSlice(c.arena, &.{ "bash", "--noprofile", "--norc", "--" }) catch return error.Unexpected;
    argv.appendSlice(c.arena, args) catch return error.Unexpected;
    const stripped = envmap.stripStartup(c.arena, env) catch return error.Unexpected;
    return runCaptured(c, argv.items, stripped, null, c.limit != null, why);
}

// ---- probes ------------------------------------------------------------

pub const CpuProbe = struct {
    status: enum { ok, none, @"error" },
    target: []const u8 = "",
    candidates: []const []const u8 = &.{},
    err: []const u8 = "",
    override: []const u8 = "",
};

pub const AccelProbe = struct {
    status: enum { ok, none, @"error" },
    subdir: []const u8 = "",
    err: []const u8 = "",
};

fn isFile(path: []const u8) bool {
    const st = fsx.statAt(fsx.cwd, path, true) catch return false;
    return st.isReg();
}

fn isDir(path: []const u8) bool {
    const st = fsx.statAt(fsx.cwd, path, true) catch return false;
    return st.isDir();
}

/// The first line of text as str.splitlines() splits it.
fn firstLine(s: []const u8) ?[]const u8 {
    if (s.len == 0) return null;
    var i: usize = 0;
    while (i < s.len) {
        const ch = text.nextChar(s[i..]);
        if (ch.cp) |cp| switch (cp) {
            '\n', '\r', 0x0b, 0x0c, 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029 => return s[0..i],
            else => {},
        };
        i += ch.len;
    }
    return s;
}

/// Lines as str.splitlines() returns them.
fn splitLines(arena: Allocator, s: []const u8) Allocator.Error![]const []const u8 {
    var out: std.ArrayList([]const u8) = .empty;
    var start: usize = 0;
    var i: usize = 0;
    while (i < s.len) {
        const ch = text.nextChar(s[i..]);
        const brk = if (ch.cp) |cp| switch (cp) {
            '\n', '\r', 0x0b, 0x0c, 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029 => true,
            else => false,
        } else false;
        if (brk) {
            try out.append(arena, s[start..i]);
            i += ch.len;
            if (ch.cp.? == '\r' and i < s.len and s[i] == '\n') i += 1;
            start = i;
            continue;
        }
        i += ch.len;
    }
    if (start < s.len) try out.append(arena, s[start..]);
    return out.items;
}

fn charCount(s: []const u8) usize {
    var n: usize = 0;
    var i: usize = 0;
    while (i < s.len) : (n += 1) i += text.nextChar(s[i..]).len;
    return n;
}

/// parse_cpu_line: exactly one non-empty line without controls, split at ':'.
fn parseCpuLine(arena: Allocator, stdout: []const u8) Allocator.Error!?[]const []const u8 {
    if (std.mem.indexOfScalar(u8, stdout, 0) != null or charCount(stdout) > max_arch_stdout) return null;
    var nonempty: std.ArrayList([]const u8) = .empty;
    for (try splitLines(arena, stdout)) |l| if (l.len > 0) try nonempty.append(arena, l);
    if (nonempty.items.len != 1) return null;
    const line = nonempty.items[0];
    for (line) |b| if (b < 32) return null;
    var parts: std.ArrayList([]const u8) = .empty;
    var it = std.mem.splitScalar(u8, line, ':');
    while (it.next()) |p| try parts.append(arena, p);
    return parts.items;
}

fn pyWhitespaceByte(b: u8) bool {
    return b == ' ' or (b >= 0x09 and b <= 0x0d) or (b >= 0x1c and b <= 0x1f);
}

/// read_cpu_flags: the flags every `flags:` line of a cpuinfo file shares.
fn readCpuFlags(arena: Allocator, path: []const u8) []const []const u8 {
    if (path.len == 0) return &.{};
    const fd = sys.openat(fsx.cwd, path, .{ .NONBLOCK = true }, 0) catch return &.{};
    defer sys.close(fd);
    var data: std.ArrayList(u8) = .empty;
    var buf: [64 * 1024]u8 = undefined;
    while (data.items.len < cpuinfo_read_max) {
        const n = sys.read(fd, &buf) catch return &.{};
        if (n == 0) break;
        data.appendSlice(arena, buf[0..@min(n, cpuinfo_read_max - data.items.len)]) catch return &.{};
    }
    var common_set: ?std.StringArrayHashMapUnmanaged(void) = null;
    var it = std.mem.splitScalar(u8, data.items, '\n');
    while (it.next()) |line| {
        if (line.len < 5 or !std.ascii.eqlIgnoreCase(line[0..5], "flags")) continue;
        var i: usize = 5;
        while (i < line.len and (line[i] == ' ' or line[i] == '\t')) i += 1;
        if (i >= line.len or line[i] != ':') continue;
        i += 1;
        var set: std.StringArrayHashMapUnmanaged(void) = .empty;
        var j = i;
        while (j < line.len) {
            while (j < line.len and pyWhitespaceByte(line[j])) j += 1;
            const s = j;
            while (j < line.len and !pyWhitespaceByte(line[j])) j += 1;
            if (j > s) set.put(arena, line[s..j], {}) catch return &.{};
        }
        if (common_set) |*cs| {
            var keep: std.StringArrayHashMapUnmanaged(void) = .empty;
            for (cs.keys()) |k| if (set.contains(k)) keep.put(arena, k, {}) catch return &.{};
            cs.* = keep;
        } else common_set = set;
    }
    return if (common_set) |cs| cs.keys() else &.{};
}

fn candidateSupportsCpu(candidate: []const u8, flags: []const []const u8) bool {
    const leaf = candidate[if (std.mem.lastIndexOfScalar(u8, candidate, '/')) |i| i + 1 else 0..];
    var buf: [128]u8 = undefined;
    if (leaf.len > buf.len) return true;
    const march = buf[0..leaf.len];
    for (leaf, 0..) |ch, i| march[i] = if (ch == '-') '_' else ch;
    if (!text.inList(&waitpkg_marches, march)) return true;
    return text.inList(flags, "waitpkg");
}

fn decodeReplace(c: *Ctx, s: []const u8) []const u8 {
    return text.decodeReplace(c.arena, s) catch s;
}

fn line1(c: *Ctx, s: []const u8, limit: usize) []const u8 {
    return text.oneLine(c.arena, s, limit) catch s;
}

pub fn probeCpu(c: *Ctx, env: Map) Stop!CpuProbe {
    const prefix = env.get("EESSI_PREFIX") orelse empty;
    const os_type = env.get("EESSI_OS_TYPE") orelse empty;
    const init = env.get("EESSI_INIT_PREFIX") orelse empty;
    if (!text.safeAbsolute(prefix) or !text.safeRelSubdir(os_type) or !text.safeAbsolute(init))
        return .{ .status = .@"error", .err = "EESSI_PREFIX, EESSI_OS_TYPE, or EESSI_INIT_PREFIX is missing or unsafe" };
    const script = std.fs.path.join(c.arena, &.{ init, "eessi_archdetect.sh" }) catch return .{ .status = .@"error", .err = "out of memory" };
    if (!isFile(script)) return .{ .status = .@"error", .err = "eessi_archdetect.sh is not available" };
    var why: []const u8 = "";
    const r = runBash(c, &.{ script, "-a", "cpupath" }, env, &why) catch |err| switch (err) {
        error.Deadline, error.Terminated => |e| return e,
        else => return .{ .status = .@"error", .err = std.fmt.allocPrint(c.arena, "could not run archdetect: {s}", .{why}) catch "could not run archdetect" },
    };
    if (r.code != 0) {
        const detail = firstLine(decodeReplace(c, r.stderr)) orelse (std.fmt.allocPrint(c.arena, "exit {d}", .{exitShown(r.code)}) catch "exit");
        return .{ .status = .@"error", .err = line1(c, detail, 200) };
    }
    const parsed = (parseCpuLine(c.arena, decodeReplace(c, r.stdout)) catch null) orelse
        return .{ .status = .@"error", .err = "cpupath output is not a single line" };
    var cands: std.ArrayList([]const u8) = .empty;
    for (parsed) |p| if (p.len > 0) cands.append(c.arena, p) catch {};
    if (cands.items.len == 0) return .{ .status = .@"error", .err = "cpupath returned no candidates" };
    for (cands.items) |cand| if (!text.safeRelSubdir(cand))
        return .{ .status = .@"error", .candidates = cands.items, .err = "cpupath returned an unsafe candidate" };
    const cpuinfo = blk: {
        const v = env.get("EESSI_PROC_CPUINFO") orelse empty;
        break :blk if (v.len > 0) v else "/proc/cpuinfo";
    };
    const flags = readCpuFlags(c.arena, cpuinfo);
    var compatible: std.ArrayList([]const u8) = .empty;
    for (cands.items) |cand| if (candidateSupportsCpu(cand, flags)) compatible.append(c.arena, cand) catch {};
    const chosen = installed(c, prefix, os_type, compatible.items);
    const raw = installed(c, prefix, os_type, cands.items);
    const override = if (chosen.len > 0 and !std.mem.eql(u8, chosen, raw)) chosen else "";
    if (chosen.len > 0) return .{ .status = .ok, .target = chosen, .candidates = compatible.items, .override = override };
    return .{ .status = .none, .candidates = compatible.items };
}

/// A signal-killed child shows as a negative return code in Python.
fn exitShown(code: u32) i64 {
    return if (code > 128 and code < 128 + 65) -@as(i64, code - 128) else code;
}

fn installed(c: *Ctx, prefix: []const u8, os_type: []const u8, items: []const []const u8) []const u8 {
    for (items) |item| {
        const d = std.fs.path.join(c.arena, &.{ prefix, "software", os_type, item, "software" }) catch continue;
        if (isDir(d)) return item;
    }
    return "";
}

pub fn probeAccel(c: *Ctx, env: Map) Stop!AccelProbe {
    const init = env.get("EESSI_INIT_PREFIX") orelse empty;
    if (!text.safeAbsolute(init)) return .{ .status = .@"error", .err = "EESSI_INIT_PREFIX is missing or unsafe" };
    const script = std.fs.path.join(c.arena, &.{ init, "eessi_archdetect.sh" }) catch return .{ .status = .@"error", .err = "out of memory" };
    if (!isFile(script)) return .{ .status = .@"error", .err = "eessi_archdetect.sh is not available" };
    var why: []const u8 = "";
    const r = runBash(c, &.{ script, "accelpath" }, env, &why) catch |err| switch (err) {
        error.Deadline, error.Terminated => |e| return e,
        else => return .{ .status = .@"error", .err = std.fmt.allocPrint(c.arena, "could not run archdetect: {s}", .{why}) catch "could not run archdetect" },
    };
    if (r.code != 0) return .{ .status = .none };
    const out = std.mem.trim(u8, decodeReplace(c, r.stdout), " \t\n\r\x0b\x0c\x1c\x1d\x1e\x1f");
    if (out.len == 0) return .{ .status = .none };
    if (std.mem.indexOfAny(u8, out, "\n\r") != null or !text.accelSubdir(out))
        return .{ .status = .@"error", .err = "accelpath output is not an accelerator subdirectory" };
    return .{ .status = .ok, .subdir = out };
}

pub fn lmodInvocable(path: []const u8) bool {
    if (path.len == 0 or path[0] == '-' or text.hasLineBreakOrNul(path)) return false;
    if (!isFile(path)) return false;
    var zb: [sys.path_max:0]u8 = undefined;
    const z = sys.z(&zb, path) catch return false;
    return linux.errno(linux.faccessat(linux.AT.FDCWD, z.ptr, linux.X_OK, 0)) == .SUCCESS;
}

// ---- workspaces --------------------------------------------------------

/// _private_directory: an existing directory, not a symbolic link, owned by
/// this user with no group or other bits; its resolved path.
fn privateDirectory(c: *Ctx, path: []const u8) ?[]const u8 {
    if (!text.safeAbsolute(path)) return null;
    const fd = fsx.openDir(fsx.cwd, path, false) catch return null;
    defer sys.close(fd);
    const st = fsx.fstat(fd) catch return null;
    if (st.uid != sys.getuid() or st.mode & 0o077 != 0) return null;
    var rb: [sys.path_max]u8 = undefined;
    const real = fsx.realpathFd(fd, &rb) catch return null;
    return c.arena.dupe(u8, real) catch null;
}

pub const WorkError = error{Unavailable};

/// restore_work_base: EESSI_ARCH_GUARD_WORKDIR when set (must be private),
/// else a private TMPDIR, else in strict mode the private state directory,
/// else TMPDIR as it is, else the system temporary directory.
pub fn restoreWorkBase(c: *Ctx, env: Map, why: *[]const u8) WorkError![]const u8 {
    const explicit = env.get("EESSI_ARCH_GUARD_WORKDIR") orelse empty;
    if (explicit.len > 0) {
        if (privateDirectory(c, explicit)) |d| return d;
        why.* = "EESSI_ARCH_GUARD_WORKDIR is not an existing private owned directory";
        return error.Unavailable;
    }
    const temporary = env.get("TMPDIR") orelse empty;
    if (privateDirectory(c, temporary)) |d| return d;
    if (c.strict) {
        var st_dir = env.get("XDG_STATE_HOME") orelse empty;
        if (st_dir.len == 0) st_dir = std.fs.path.join(c.arena, &.{ env.get("HOME") orelse empty, ".local", "state" }) catch "";
        const p = std.fs.path.join(c.arena, &.{ st_dir, "eessi-arch-guard" }) catch "";
        if (privateDirectory(c, p)) |d| return d;
        why.* = "strict mode needs an existing private job or state workspace";
        return error.Unavailable;
    }
    if (text.safeAbsolute(temporary) and isDir(temporary)) return temporary;
    return pyTempDir(c, env);
}

/// tempfile.gettempdir(): TMPDIR, TEMP, TMP, then /tmp, /var/tmp, /usr/tmp,
/// then the current directory; the first that is a writable directory.
fn pyTempDir(c: *Ctx, env: Map) []const u8 {
    for ([_][]const u8{ "TMPDIR", "TEMP", "TMP" }) |k| {
        const v = env.get(k) orelse continue;
        if (v.len > 0 and writableDir(v)) return absolutize(c, v);
    }
    for ([_][]const u8{ "/tmp", "/var/tmp", "/usr/tmp" }) |d| if (writableDir(d)) return d;
    return absolutize(c, ".");
}

fn absolutize(c: *Ctx, p: []const u8) []const u8 {
    if (p.len > 0 and p[0] == '/') return p;
    var rb: [sys.path_max]u8 = undefined;
    const cwd = fsx.realpath(fsx.cwd, ".", &rb) catch return p;
    return std.fs.path.join(c.arena, &.{ cwd, p }) catch p;
}

fn writableDir(p: []const u8) bool {
    if (!isDir(p)) return false;
    var zb: [sys.path_max:0]u8 = undefined;
    const z = sys.z(&zb, p) catch return false;
    return linux.errno(linux.faccessat(linux.AT.FDCWD, z.ptr, linux.W_OK | linux.X_OK, 0)) == .SUCCESS;
}

/// tempfile.mkdtemp(prefix="eessi-arch-guard-", dir=base): a new 0700
/// directory with a random suffix.
fn mkdtemp(c: *Ctx, base: []const u8) ?[]const u8 {
    const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789_";
    var tries: usize = 0;
    while (tries < 100) : (tries += 1) {
        var rnd: [8]u8 = undefined;
        _ = linux.getrandom(&rnd, rnd.len, 0);
        var suffix: [8]u8 = undefined;
        for (rnd, 0..) |b, i| suffix[i] = alphabet[b % alphabet.len];
        const p = std.fmt.allocPrint(c.arena, "{s}/eessi-arch-guard-{s}", .{ base, suffix }) catch return null;
        var zb: [sys.path_max:0]u8 = undefined;
        const z = sys.z(&zb, p) catch return null;
        switch (linux.errno(linux.mkdirat(linux.AT.FDCWD, z.ptr, 0o700))) {
            .SUCCESS => return p,
            .EXIST => continue,
            else => return null,
        }
    }
    return null;
}

fn rmtree(c: *Ctx, p: []const u8) void {
    fsx.deleteTree(c.arena, fsx.cwd, p) catch {};
}

// ---- the restore helper, caller side -------------------------------------

pub const Restore = struct {
    ok: bool,
    env: ?Map = null,
    missing: []const []const u8 = &.{},
    err: []const u8 = "",
};

fn wireMap(c: *Ctx, out: *std.ArrayList(u8), m: Map) Allocator.Error!void {
    try out.append(c.arena, '{');
    var it = m.iterator();
    var first = true;
    while (it.next()) |kv| {
        if (!first) try out.appendSlice(c.arena, ", ");
        first = false;
        try json.writeWireString(out, c.arena, kv.key_ptr.*);
        try out.appendSlice(c.arena, ": ");
        try json.writeWireString(out, c.arena, kv.value_ptr.*);
    }
    try out.append(c.arena, '}');
}

fn callRestore(c: *Ctx, env: Map, eessi: []const u8, modules: []const []const u8) Stop!Restore {
    const a = c.arena;
    var req: std.ArrayList(u8) = .empty;
    buildRequest(c, &req, env, eessi, modules) catch return .{ .ok = false, .err = "out of memory" };
    var why: []const u8 = "";
    const base = restoreWorkBase(c, env, &why) catch
        return .{ .ok = false, .err = std.fmt.allocPrint(a, "restore workspace unavailable: {s}", .{why}) catch "restore workspace unavailable" };
    const work = mkdtemp(c, base) orelse return .{ .ok = false, .err = "restore workspace unavailable: could not create a directory" };
    defer rmtree(c, work);
    var clean: Map = .empty;
    clean.put(a, "PATH", "/usr/bin:/bin") catch {};
    clean.put(a, "LC_ALL", "C") catch {};
    clean.put(a, "LANG", "C") catch {};
    clean.put(a, "HOME", work) catch {};
    clean.put(a, "TMPDIR", work) catch {};
    clean.put(a, "EESSI_ARCH_GUARD_WORKDIR", work) catch {};
    var argv: std.ArrayList([]const u8) = .empty;
    argv.appendSlice(a, &.{ c.self_exe, "--restore" }) catch {};
    if (c.strict) argv.append(a, "--strict") catch {};
    const r = runCaptured(c, argv.items, clean, req.items, c.limit != null, &why) catch |err| switch (err) {
        error.Deadline, error.Terminated => |e| return e,
        else => return .{ .ok = false, .err = std.fmt.allocPrint(a, "could not start restore helper: {s}", .{why}) catch "could not start restore helper" },
    };
    if (r.code != 0 or r.stdout.len == 0) {
        const e = decodeReplace(c, r.stderr);
        return .{ .ok = false, .err = line1(c, if (e.len > 0) e else "restore helper failed", 300) };
    }
    const data = json.parse(a, r.stdout) catch return .{ .ok = false, .err = "restore helper returned invalid output" };
    var missing: std.ArrayList([]const u8) = .empty;
    if (data.get("missing")) |m| if (m == .array) for (m.array) |item| {
        if (item != .string) continue;
        const name = json.decodeCache(a, item.string) catch continue;
        if (text.moduleName(name)) missing.append(a, name) catch {};
    };
    const ok = if (data.get("ok")) |v| v == .bool and v.bool else false;
    if (!ok) {
        const e: []const u8 = if (data.get("error")) |v| (if (v == .string) json.decodeCache(a, v.string) catch "" else "") else "";
        return .{ .ok = false, .missing = missing.items, .err = line1(c, e, 300) };
    }
    const restored = data.get("env") orelse return .{ .ok = false, .missing = missing.items, .err = "restore helper returned no environment" };
    if (restored != .object) return .{ .ok = false, .missing = missing.items, .err = "restore helper returned no environment" };
    var m: Map = .empty;
    for (restored.object) |mem| {
        const k = json.decodeWire(a, mem.key) catch return .{ .ok = false, .missing = missing.items, .err = "restore helper returned no environment" };
        if (mem.value != .string) return .{ .ok = false, .missing = missing.items, .err = "restore helper returned no environment" };
        const v = json.decodeWire(a, mem.value.string) catch return .{ .ok = false, .missing = missing.items, .err = "restore helper returned no environment" };
        m.put(a, k, v) catch {};
    }
    return .{ .ok = true, .env = m, .missing = missing.items };
}

fn buildRequest(c: *Ctx, out: *std.ArrayList(u8), env: Map, eessi: []const u8, modules: []const []const u8) Allocator.Error!void {
    const a = c.arena;
    try out.appendSlice(a, "{\"v\": 1, \"env\": ");
    try wireMap(c, out, env);
    try out.appendSlice(a, ", \"eessi\": ");
    try json.writeWireString(out, a, eessi);
    try out.appendSlice(a, ", \"modules\": [");
    for (modules, 0..) |m, i| {
        if (i > 0) try out.appendSlice(a, ", ");
        try json.writeWireString(out, a, m);
    }
    try out.appendSlice(a, "], \"lmod\": ");
    try json.writeWireString(out, a, env.get("LMOD_CMD") orelse empty);
    try out.append(a, '}');
}

// ---- the restore helper, helper side -------------------------------------

const apply_sh = "source \"$1\" >/dev/null || exit 4\nexec env -0\n";

const Applied = struct { env: ?Map, err: []const u8 };

fn writePrivateNew(path: []const u8, data: []const u8, mode: u32) bool {
    const fd = sys.openat(fsx.cwd, path, .{ .ACCMODE = .WRONLY, .CREAT = true, .EXCL = true, .NOFOLLOW = true }, 0o600) catch return false;
    defer sys.close(fd);
    sys.writeAll(fd, data) catch return false;
    if (mode != 0o600) sys.fchmod(fd, mode) catch return false;
    return true;
}

fn parseEnv0(c: *Ctx, blob: []const u8) Map {
    var m: Map = .empty;
    var it = std.mem.splitScalar(u8, blob, 0);
    while (it.next()) |part| {
        const eq = std.mem.indexOfScalar(u8, part, '=') orelse continue;
        m.put(c.arena, part[0..eq], part[eq + 1 ..]) catch {};
    }
    return m;
}

const HelperError = error{Raise};

fn sourceExports(c: *Ctx, module_env: Map, script: []const u8, raised: *[]const u8) HelperError!Applied {
    var why: []const u8 = "";
    const base = restoreWorkBase(c, c.env, &why) catch {
        raised.* = why;
        return error.Raise;
    };
    const tmp = mkdtemp(c, base) orelse {
        raised.* = "could not create a temporary directory";
        return error.Raise;
    };
    defer rmtree(c, tmp);
    const fragment = std.fs.path.join(c.arena, &.{ tmp, "fragment.sh" }) catch return error.Raise;
    const apply = std.fs.path.join(c.arena, &.{ tmp, "apply.sh" }) catch return error.Raise;
    if (!writePrivateNew(fragment, script, 0o600) or !writePrivateNew(apply, apply_sh, 0o700)) {
        raised.* = "could not write the restore scripts";
        return error.Raise;
    }
    const stripped = envmap.stripStartup(c.arena, module_env) catch return error.Raise;
    const r = runCaptured(c, &.{ "bash", "--noprofile", "--norc", "--", apply, fragment }, stripped, null, false, &why) catch {
        raised.* = why;
        return error.Raise;
    };
    if (r.code != 0) {
        const e = decodeReplace(c, r.stderr);
        return .{ .env = null, .err = if (e.len > 0) e else std.fmt.allocPrint(c.arena, "apply exited {d}", .{exitShown(r.code)}) catch "apply failed" };
    }
    return .{ .env = parseEnv0(c, r.stdout), .err = "" };
}

fn applyLmod(c: *Ctx, module_env: Map, lmod: []const u8, args: []const []const u8, raised: *[]const u8) HelperError!Applied {
    var argv: std.ArrayList([]const u8) = .empty;
    argv.appendSlice(c.arena, &.{ lmod, "bash" }) catch return error.Raise;
    argv.appendSlice(c.arena, args) catch return error.Raise;
    const stripped = envmap.stripStartup(c.arena, module_env) catch return error.Raise;
    var why: []const u8 = "";
    const r = runCaptured(c, argv.items, stripped, null, false, &why) catch {
        raised.* = why;
        return error.Raise;
    };
    if (r.code != 0) return .{ .env = null, .err = decodeReplace(c, r.stderr) };
    return sourceExports(c, module_env, r.stdout, raised);
}

fn diagnose(c: *Ctx, original: Map, lmod: []const u8, eessi: []const u8, modules: []const []const u8, raised: *[]const u8) HelperError![]const []const u8 {
    var missing: std.ArrayList([]const u8) = .empty;
    for (modules[0..@min(modules.len, diagnose_limit)]) |name| {
        const p = try applyLmod(c, original, lmod, &.{ "--force", "purge" }, raised);
        const purged = p.env orelse {
            missing.append(c.arena, name) catch {};
            continue;
        };
        const e = try applyLmod(c, purged, lmod, &.{ "load", eessi }, raised);
        const with_eessi = e.env orelse {
            missing.append(c.arena, name) catch {};
            continue;
        };
        const l = try applyLmod(c, with_eessi, lmod, &.{ "load", name }, raised);
        if (l.env == null) missing.append(c.arena, name) catch {};
    }
    return missing.items;
}

const Reply = struct {
    ok: bool,
    env: ?Map = null,
    missing: []const []const u8 = &.{},
    err: []const u8 = "",
};

fn malformed() Reply {
    return .{ .ok = false, .err = "restore request is malformed" };
}

fn doRestore(c: *Ctx, payload: json.Value, raised: *[]const u8) HelperError!Reply {
    const a = c.arena;
    const env_v = payload.get("env") orelse return malformed();
    const eessi_v = payload.get("eessi") orelse return malformed();
    const modules_v = payload.get("modules") orelse return malformed();
    const lmod_v = payload.get("lmod") orelse return malformed();
    if (env_v != .object or eessi_v != .string or modules_v != .array or lmod_v != .string) return malformed();
    const eessi = json.decodeWire(a, eessi_v.string) catch return malformed();
    const lmod = json.decodeWire(a, lmod_v.string) catch return malformed();
    var modules: std.ArrayList([]const u8) = .empty;
    var module_bad = false;
    for (modules_v.array) |m| {
        if (m != .string) {
            module_bad = true;
            continue;
        }
        modules.append(a, json.decodeWire(a, m.string) catch return malformed()) catch {};
    }
    var module_env: Map = .empty;
    for (env_v.object) |mem| {
        const k = json.decodeWire(a, mem.key) catch return malformed();
        const v = switch (mem.value) {
            .string => |s| json.decodeWire(a, s) catch return malformed(),
            // str(value) of a non-string JSON value.
            .null => "None",
            .bool => |b| if (b) "True" else "False",
            .number => |n| n,
            else => return malformed(),
        };
        module_env.put(a, k, v) catch {};
    }
    if (!lmodInvocable(lmod)) return .{ .ok = false, .err = "LMOD_CMD is not executable" };
    if (!text.moduleName(eessi) or !text.eessiModule(eessi)) return .{ .ok = false, .err = "unsafe-module-name" };
    if (module_bad) return .{ .ok = false, .err = "unsafe-module-name" };
    for (modules.items) |m| if (!text.moduleName(m)) return .{ .ok = false, .err = "unsafe-module-name" };

    const p = try applyLmod(c, module_env, lmod, &.{ "--force", "purge" }, raised);
    var current = p.env orelse return .{ .ok = false, .err = line1(c, if (p.err.len > 0) p.err else "purge failed", 300) };
    const e = try applyLmod(c, current, lmod, &.{ "load", eessi }, raised);
    current = e.env orelse {
        const miss = a.dupe([]const u8, &.{eessi}) catch &.{};
        return .{ .ok = false, .missing = miss, .err = line1(c, if (e.err.len > 0) e.err else "EESSI load failed", 300) };
    };
    if (modules.items.len > 0) {
        var args: std.ArrayList([]const u8) = .empty;
        args.append(a, "load") catch {};
        args.appendSlice(a, modules.items) catch {};
        const n = try applyLmod(c, current, lmod, args.items, raised);
        current = n.env orelse {
            const miss = try diagnose(c, module_env, lmod, eessi, modules.items, raised);
            return .{ .ok = false, .missing = miss, .err = line1(c, if (n.err.len > 0) n.err else "module load failed", 300) };
        };
    }
    return .{ .ok = true, .env = current };
}

/// restore_main: one JSON request on stdin, one JSON reply on stdout; 1 with
/// a message on stderr when the request cannot be handled at all.
pub fn restoreMain(c: *Ctx) u8 {
    const a = c.arena;
    const input = fsx.readAll(a, 0, 256 << 20) catch {
        writeErr("eessi-arch-guard: restore failed: could not read the request\n");
        return 1;
    };
    // json.loads(bytes.decode("utf-8")): the request must be UTF-8 text.
    if (!std.unicode.utf8ValidateSlice(input)) {
        writeErr("eessi-arch-guard: restore failed: the request is not UTF-8\n");
        return 1;
    }
    const payload = json.parse(a, input) catch {
        writeErr("eessi-arch-guard: restore failed: the request is not JSON\n");
        return 1;
    };
    if (payload != .object) {
        writeErr("eessi-arch-guard: restore failed: the request is not a JSON object\n");
        return 1;
    }
    var raised: []const u8 = "";
    const reply = doRestore(c, payload, &raised) catch {
        const msg = std.fmt.allocPrint(a, "eessi-arch-guard: restore failed: {s}\n", .{line1(c, raised, 300)}) catch "eessi-arch-guard: restore failed\n";
        writeErr(msg);
        return 1;
    };
    var out: std.ArrayList(u8) = .empty;
    writeReply(c, &out, reply) catch return 1;
    common.cli.put(1, out.items);
    return 0;
}

fn writeReply(c: *Ctx, out: *std.ArrayList(u8), r: Reply) Allocator.Error!void {
    const a = c.arena;
    try out.appendSlice(a, if (r.ok) "{\"ok\": true" else "{\"ok\": false");
    if (r.env) |e| {
        try out.appendSlice(a, ", \"env\": ");
        try wireMap(c, out, e);
    }
    try out.appendSlice(a, ", \"missing\": [");
    for (r.missing, 0..) |m, i| {
        if (i > 0) try out.appendSlice(a, ", ");
        try json.writeCacheString(out, a, m);
    }
    try out.appendSlice(a, "], \"error\": ");
    try json.writeCacheString(out, a, r.err);
    try out.append(a, '}');
}

fn writeErr(s: []const u8) void {
    common.cli.put(2, s);
}

// ---- the decision ------------------------------------------------------

fn side(s: []const u8) []const u8 {
    if (s.len == 0 or text.hasLineBreakOrNul(s)) return "unrecognized";
    return s;
}

fn failed(c: *Ctx, was: []const u8, now: []const u8, message: []const u8, detail: ?[]const u8) Outcome {
    var ch: Changes = .empty;
    const token = std.fmt.allocPrint(c.arena, "{s}->{s}", .{ side(was), side(now) }) catch "unrecognized->unrecognized";
    ch.put(c.arena, "EESSI_ARCH_GUARD_FAILED", token) catch {};
    return .{
        .changes = ch,
        .message = line1(c, message, 500),
        .kind = .failed,
        .detail = if (detail) |d| (if (d.len > 0) line1(c, d, 500) else null) else null,
    };
}

fn fmt(c: *Ctx, comptime f: []const u8, args: anytype) []const u8 {
    return std.fmt.allocPrint(c.arena, f, args) catch "eessi-arch-guard: out of memory";
}

const Loaded = struct { eessi: []const u8 = "", others: []const []const u8 = &.{}, err: []const u8 = "" };

fn parseLoaded(c: *Ctx, env: Map) Loaded {
    const raw = env.get("LOADEDMODULES") orelse empty;
    var names: std.ArrayList([]const u8) = .empty;
    var it = std.mem.splitScalar(u8, raw, ':');
    while (it.next()) |p| if (p.len > 0) names.append(c.arena, p) catch {};
    if (names.items.len == 0) return .{ .err = "LOADEDMODULES is empty" };
    for (names.items) |n| if (!text.moduleName(n)) return .{ .err = "unsafe-module-name" };
    var eessi: ?[]const u8 = null;
    var count: usize = 0;
    for (names.items) |n| if (std.mem.startsWith(u8, n, "EESSI/")) {
        eessi = n;
        count += 1;
    };
    if (count != 1 or !text.eessiModule(eessi.?)) return .{ .err = "eessi-module-name" };
    var others: std.ArrayList([]const u8) = .empty;
    for (names.items) |n| if (!std.mem.eql(u8, n, eessi.?)) others.append(c.arena, n) catch {};
    return .{ .eessi = eessi.?, .others = others.items };
}

fn restoreMismatch(new: Map, now: []const u8, now_accel: []const u8, compare_accel: bool, eessi: []const u8, others: []const []const u8, c: *Ctx) []const u8 {
    if (!std.mem.eql(u8, new.get("EESSI_SOFTWARE_SUBDIR") orelse empty, now)) return "reload did not select this node's CPU target";
    if (compare_accel and !std.mem.eql(u8, new.get("EESSI_ACCEL_SUBDIR") orelse empty, now_accel)) return "reload did not select this node's accelerator target";
    var loaded: std.ArrayList([]const u8) = .empty;
    var it = std.mem.splitScalar(u8, new.get("LOADEDMODULES") orelse empty, ':');
    while (it.next()) |p| if (p.len > 0) loaded.append(c.arena, p) catch {};
    if (!text.inList(loaded.items, eessi)) return "reload did not load the EESSI module";
    for (others) |n| if (!text.inList(loaded.items, n)) return fmt(c, "reload did not load {s}", .{n});
    return "";
}

fn nonEmpty(env: Map, k: []const u8) bool {
    return (env.get(k) orelse empty).len > 0;
}

pub fn decide(c: *Ctx, env: Map, shell: bool) Stop!Outcome {
    if (c.limit) |l| try l.check();
    if (std.mem.eql(u8, env.get("EESSI_ARCH_GUARD") orelse empty, "0"))
        return .{ .kind = .skip, .detail = "EESSI_ARCH_GUARD=0" };
    const was = env.get("EESSI_SOFTWARE_SUBDIR") orelse empty;
    if (was.len == 0) return .{ .kind = .skip, .detail = "EESSI is not loaded" };
    if (nonEmpty(env, "EESSI_SOFTWARE_SUBDIR_OVERRIDE")) return .{ .kind = .skip, .detail = "EESSI_SOFTWARE_SUBDIR_OVERRIDE is set" };
    if (nonEmpty(env, "EESSI_ARCHDETECT_OPTIONS_OVERRIDE")) return .{ .kind = .skip, .detail = "EESSI_ARCHDETECT_OPTIONS_OVERRIDE is set" };

    const cpu = try probeCpu(c, env);
    if (cpu.status == .@"error")
        return failed(c, was, "archdetect-failed", fmt(c, "eessi-arch-guard: archdetect failed ({s}); environment left as it was", .{cpu.err}), null);
    if (cpu.status == .none) {
        if (std.mem.eql(u8, env.get("EESSI_ARCH_GUARD_MISSING") orelse empty, "ignore"))
            return .{ .kind = .skip, .detail = "no EESSI software directory (ignored)" };
        const shown = if (cpu.candidates.len > 0) std.mem.join(c.arena, " ", cpu.candidates) catch "(none)" else "(none)";
        return failed(c, was, "no-software-dir", fmt(c, "eessi-arch-guard: no EESSI software directory for this node under {s} (candidates {s}); modules were loaded for {s}; environment left as it was", .{ env.get("EESSI_PREFIX") orelse empty, shown, was }), null);
    }

    const compare_accel = !nonEmpty(env, "EESSI_ACCELERATOR_TARGET_OVERRIDE");
    const was_accel = env.get("EESSI_ACCEL_SUBDIR") orelse empty;
    var now_accel = was_accel;
    if (compare_accel) {
        const accel = try probeAccel(c, env);
        if (accel.status == .@"error")
            return failed(c, was, "archdetect-failed", fmt(c, "eessi-arch-guard: archdetect failed ({s}); environment left as it was", .{accel.err}), null);
        now_accel = accel.subdir;
    }
    const now = cpu.target;
    if (std.mem.eql(u8, now, was) and std.mem.eql(u8, now_accel, was_accel))
        return .{ .kind = .noop, .detail = "target already matches this node" };

    var was_label = was;
    var now_label = now;
    if (compare_accel and !std.mem.eql(u8, was_accel, now_accel)) {
        was_label = fmt(c, "{s} {s}", .{ was, if (was_accel.len > 0) was_accel else "no accel" });
        now_label = fmt(c, "{s} {s}", .{ now, if (now_accel.len > 0) now_accel else "no accel" });
    }

    const loaded = parseLoaded(c, env);
    if (loaded.err.len > 0) {
        const unsafe_name = std.mem.eql(u8, loaded.err, "unsafe-module-name");
        const why = if (unsafe_name) "a loaded module name is not safe to pass to Lmod" else "the loaded module list has no single EESSI/<version> entry";
        return failed(c, was_label, if (unsafe_name) "unsafe-module-name" else "restore-failed", fmt(c, "eessi-arch-guard: {s}; environment left as it was", .{why}), null);
    }
    if (!lmodInvocable(env.get("LMOD_CMD") orelse empty))
        return failed(c, was_label, "lmod-unavailable", fmt(c, "eessi-arch-guard: LMOD_CMD is missing or not executable; this node is {s}, the modules were loaded for {s}; environment left as it was", .{ now_label, was_label }), null);

    var module_env = env;
    if (cpu.override.len > 0) {
        module_env = env.clone(c.arena) catch env;
        _ = module_env.orderedRemove("EESSI_ARCHDETECT_OPTIONS");
        module_env.put(c.arena, "EESSI_SOFTWARE_SUBDIR_OVERRIDE", cpu.override) catch {};
    }
    const result = try callRestore(c, module_env, loaded.eessi, loaded.others);
    if (!result.ok or result.env == null) {
        var why: []const u8 = "module load failed";
        if (result.missing.len > 0) {
            var names = std.mem.join(c.arena, " ", result.missing) catch "";
            if (loaded.others.len > diagnose_limit) names = fmt(c, "{s} (stopped after {d})", .{ names, diagnose_limit });
            why = fmt(c, "not available for this target: {s}", .{names});
        }
        return failed(c, was_label, now_label, fmt(c, "eessi-arch-guard: this node is {s}, the modules were loaded for {s}; {s}; environment left as it was", .{ now_label, was_label, why }), result.err);
    }
    const mismatch = restoreMismatch(result.env.?, now, now_accel, compare_accel, loaded.eessi, loaded.others, c);
    if (mismatch.len > 0)
        return failed(c, was_label, "restore-failed", fmt(c, "eessi-arch-guard: {s}; environment left as it was", .{mismatch}), null);

    var d = (envmap.delta(c.arena, env, result.env.?, shell) catch null) orelse
        return failed(c, was_label, "unsafe-environment", "eessi-arch-guard: reload produced a value this output mode cannot carry; environment left as it was", null);
    d.put(c.arena, "EESSI_ARCH_GUARD_RELOADED", fmt(c, "{s}->{s}", .{ side(was_label), side(now_label) })) catch {};
    if (env.get("EESSI_ARCH_GUARD_FAILED") != null and !d.contains("EESSI_ARCH_GUARD_FAILED"))
        d.put(c.arena, "EESSI_ARCH_GUARD_FAILED", null) catch {};
    const message = fmt(c, "eessi-arch-guard: reloaded modules for {s} (loaded for {s})", .{ now_label, was_label });
    if (!shell) {
        _ = envmap.render(c.arena, d, message, false, true) catch
            return failed(c, was_label, "unsafe-environment", "eessi-arch-guard: reload produced a prolog line past the Slurm 17.11 read; environment left as it was", null);
    }
    return .{ .changes = d, .message = message, .kind = .reloaded };
}

// ---- the cache ---------------------------------------------------------

fn cacheDigest(c: *Ctx, env: Map) []const u8 {
    var h = std.crypto.hash.sha2.Sha256.init(.{});
    for (key_fields, 0..) |k, i| {
        if (i > 0) h.update("\x00");
        h.update(env.get(k) orelse empty);
    }
    var out: [32]u8 = undefined;
    h.final(&out);
    const hex = std.fmt.bytesToHex(out, .lower);
    return c.arena.dupe(u8, hex[0..16]) catch "0000000000000000";
}

fn safeComponent(c: *Ctx, s: []const u8) []const u8 {
    if (text.safeComponent(s)) return s;
    var out: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(s, &out, .{});
    const hex = std.fmt.bytesToHex(out, .lower);
    return c.arena.dupe(u8, hex[0..16]) catch "0000000000000000";
}

fn cacheDirectory(c: *Ctx, env: Map) ?[]const u8 {
    if (c.strict) {
        var why: []const u8 = "";
        return restoreWorkBase(c, env, &why) catch null;
    }
    const tmp = env.get("TMPDIR") orelse empty;
    if (tmp.len > 0 and text.safeAbsolute(tmp) and isDir(tmp)) return tmp;
    if (isDir("/tmp")) return "/tmp";
    return null;
}

const CachePath = struct { dir: []const u8, name: []const u8 };

/// cache_file_path: .eessi-arch-guard.<uid>.<node>.<job>.<step>.<mode>.<digest>
fn cachePath(c: *Ctx, env: Map, shell: bool) ?CachePath {
    const job = env.get("SLURM_JOB_ID") orelse empty;
    if (job.len == 0) return null;
    const dir = cacheDirectory(c, env) orelse return null;
    const step = env.get("SLURM_STEP_ID") orelse "batch";
    var uts: linux.utsname = undefined;
    _ = linux.uname(&uts);
    const node = std.mem.sliceTo(&uts.nodename, 0);
    const name = std.fmt.allocPrint(c.arena, ".eessi-arch-guard.{d}.{s}.{s}.{s}.{s}.{s}", .{
        sys.getuid(),
        safeComponent(c, node),
        safeComponent(c, job),
        safeComponent(c, step),
        if (shell) "shell" else "prolog",
        cacheDigest(c, env),
    }) catch return null;
    return .{ .dir = dir, .name = name };
}

fn kindName(k: Kind) []const u8 {
    return @tagName(k);
}

fn readCache(c: *Ctx, dirfd: sys.fd_t, name: []const u8, shell: bool) ?Outcome {
    const a = c.arena;
    const blob = (state.readPrivate(a, dirfd, name, cache_read_max) catch return null) orelse return null;
    if (!std.unicode.utf8ValidateSlice(blob)) return null;
    const data = json.parse(a, blob) catch return null;
    if (data != .object) return null;
    // data.get("v") != 1 in Python: 1, 1.0 and true all equal 1.
    const v = data.get("v") orelse return null;
    switch (v) {
        .number => |n| if ((std.fmt.parseFloat(f64, n) catch return null) != 1.0) return null,
        .bool => |b| if (!b) return null,
        else => return null,
    }
    var message: ?[]const u8 = null;
    if (data.get("message")) |m| switch (m) {
        .null => {},
        .string => |s| {
            const t = json.decodeCache(a, s) catch return null;
            if (std.mem.indexOfAny(u8, t, "\n\r\x00") != null) return null;
            message = t;
        },
        else => return null,
    };
    var detail: ?[]const u8 = null;
    if (data.get("detail")) |d| switch (d) {
        .null => {},
        .string => |s| detail = json.decodeCache(a, s) catch return null,
        else => return null,
    };
    const ch = data.get("changes") orelse return null;
    if (ch != .object) return null;
    var changes: Changes = .empty;
    for (ch.object) |mem| {
        const key = json.decodeCache(a, mem.key) catch return null;
        if (!text.envName(key) or text.neverCache(key)) return null;
        switch (mem.value) {
            .null => changes.put(a, key, null) catch return null,
            .string => |s| {
                const val = json.decodeCache(a, s) catch return null;
                if (!text.valueEmitOk(val, shell)) return null;
                changes.put(a, key, val) catch return null;
            },
            else => return null,
        }
    }
    const k = data.get("kind") orelse return null;
    if (k != .string) return null;
    const kind = std.meta.stringToEnum(Kind, k.string) orelse return null;
    return .{ .changes = changes, .message = message, .kind = kind, .detail = detail };
}

fn writeCache(c: *Ctx, dirfd: sys.fd_t, name: []const u8, o: Outcome) void {
    const a = c.arena;
    var out: std.ArrayList(u8) = .empty;
    (struct {
        fn w(arena: Allocator, buf: *std.ArrayList(u8), oc: Outcome) Allocator.Error!void {
            try buf.appendSlice(arena, "{\"v\": 1, \"kind\": ");
            try json.writeCacheString(buf, arena, kindName(oc.kind));
            try buf.appendSlice(arena, ", \"message\": ");
            if (oc.message) |m| try json.writeCacheString(buf, arena, m) else try buf.appendSlice(arena, "null");
            try buf.appendSlice(arena, ", \"detail\": ");
            if (oc.detail) |d| try json.writeCacheString(buf, arena, d) else try buf.appendSlice(arena, "null");
            try buf.appendSlice(arena, ", \"changes\": {");
            var it = oc.changes.iterator();
            var first = true;
            while (it.next()) |kv| {
                if (!first) try buf.appendSlice(arena, ", ");
                first = false;
                try json.writeCacheString(buf, arena, kv.key_ptr.*);
                try buf.appendSlice(arena, ": ");
                if (kv.value_ptr.*) |v| try json.writeCacheString(buf, arena, v) else try buf.appendSlice(arena, "null");
            }
            try buf.appendSlice(arena, "}}\n");
        }
    }).w(a, &out, o) catch return;
    state.writePrivate(a, dirfd, name, out.items) catch {};
}

fn lockExclusive(c: *Ctx, fd: sys.fd_t) Stop!void {
    const l = c.limit orelse {
        state.lockExclusive(fd, null) catch {};
        return;
    };
    while (true) {
        try l.check();
        const rc = linux.flock(fd, std.posix.LOCK.EX | std.posix.LOCK.NB);
        if (linux.errno(rc) == .SUCCESS) return;
        const pause = @max(@min(lock_poll_ms, l.remainingMs()), 0);
        var ts: linux.timespec = .{ .sec = 0, .nsec = @intCast(pause * std.time.ns_per_ms) };
        _ = linux.nanosleep(&ts, null);
    }
}

pub fn cachedDecide(c: *Ctx, env: Map, shell: bool) Stop!Outcome {
    const p = cachePath(c, env, shell) orelse return decide(c, env, shell);
    const dirfd = fsx.openDir(fsx.cwd, p.dir, true) catch return decide(c, env, shell);
    defer sys.close(dirfd);
    const lock_name = std.fmt.allocPrint(c.arena, "{s}.lock", .{p.name}) catch return decide(c, env, shell);
    // A lock file someone else made is neither locked on nor changed:
    // decide without the cache.
    const lockfd = (state.openPrivate(dirfd, lock_name, true) catch return decide(c, env, shell)) orelse return decide(c, env, shell);
    defer sys.close(lockfd);
    try lockExclusive(c, lockfd);
    if (readCache(c, dirfd, p.name, shell)) |o| return o;
    if (c.limit) |l| try l.check();
    const outcome = try decide(c, env, shell);
    if (c.limit) |l| l.committing.store(true, .seq_cst);
    defer if (c.limit) |l| l.committing.store(false, .seq_cst);
    writeCache(c, dirfd, p.name, outcome);
    return outcome;
}

/// The decision under one deadline. A timeout or SIGTERM is not cached.
pub fn limitedDecide(c: *Ctx, env: Map, shell: bool, timeout_s: f64) Outcome {
    var limit: Limit = .{ .deadline_ms = nowMs() + @as(i64, @intFromFloat(@min(timeout_s * 1000.0, 9.2e15))) };
    c.limit = &limit;
    installLimit(&limit);
    defer {
        removeLimit(&limit);
        c.limit = null;
    }
    const was = env.get("EESSI_SOFTWARE_SUBDIR") orelse empty;
    return cachedDecide(c, env, shell) catch |err| switch (err) {
        error.Deadline => {
            if (cachePath(c, env, shell)) |p| {
                if (fsx.openDir(fsx.cwd, p.dir, true)) |dirfd| {
                    defer sys.close(dirfd);
                    if (readCache(c, dirfd, p.name, shell)) |o| return o;
                } else |_| {}
            }
            return failed(c, was, "timeout", "eessi-arch-guard: decision exceeded the time limit; environment left as it was", null);
        },
        error.Terminated => return failed(c, was, "terminated", "eessi-arch-guard: decision was terminated; environment left as it was", null),
    };
}
