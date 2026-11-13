//! eessi-arch-guard: a Slurm TaskProlog that reloads the EESSI module
//! environment when the loaded CPU or accelerator target is not this
//! node's. It exits 0 on every path a prolog can take, unless --strict asks
//! for 78 on a failed decision: a panic, a crash signal or a failed write
//! prints one skip line instead of killing the task.
const std = @import("std");
const linux = std.os.linux;
const build_options = @import("build_options");
const common = @import("zig_common");
const text = @import("text.zig");
const envmap = @import("envmap.zig");
const guard = @import("guard.zig");

const sys = common.sys;
const fsx = common.fsx;
const proc = common.proc;
const cli = common.cli;
const Allocator = std.mem.Allocator;
const empty: []const u8 = "";

pub const version = build_options.version;
const strict_failure_exit: u8 = 78;
const default_timeout: f64 = 60.0;
/// threading.TIMEOUT_MAX on Linux, as Python prints it.
const timeout_max: f64 = 9223372036.0;
const timeout_max_text = "9223372036.0";

const help_text =
    \\usage: eessi-arch-guard [--shell] [--strict] [--chain PATH] [--timeout SEC]
    \\                        [--restore] [-h|--help] [--version]
    \\
    \\Slurm TaskProlog: when the loaded EESSI CPU or accelerator target is not this
    \\node's, print export/unset lines for the reloaded module environment.
    \\--shell prints bash for: eval "$(eessi-arch-guard --shell)"
    \\--strict returns 78 on a failed decision or an unexpected exception.
    \\--chain PATH runs an existing task prolog first. Slurm does not pass arguments,
    \\so a site sets this in a wrapper or in a root-owned eessi-arch-guard.conf.
    \\--timeout SEC bounds the decision. The default is 60 seconds.
    \\--restore reads a JSON restore request on stdin and writes the JSON result.
    \\-h, --help print this text. --version prints the version.
    \\
;

// ---- the exit-0 guarantee ------------------------------------------------

/// Set before anything can fail: where the skip line goes and what to exit.
var emergency_shell = false;
var emergency_strict = false;

fn emergencyWrite(kind: []const u8, detail: []const u8) void {
    var buf: [1024]u8 = undefined;
    const msg = std.fmt.bufPrint(&buf, "eessi-arch-guard: skipped, {s}: {s}", .{ kind, detail }) catch "eessi-arch-guard: skipped";
    // No allocation here: a crash may have come from the allocator.
    var clean: [1024]u8 = undefined;
    var n: usize = 0;
    for (msg) |ch| {
        clean[n] = if (ch < 32 or ch == 127) ' ' else ch;
        n += 1;
    }
    if (emergency_shell) {
        cli.put(2, clean[0..n]);
        cli.put(2, "\n");
    } else {
        cli.put(1, "print ");
        cli.put(1, clean[0..n]);
        cli.put(1, "\n");
    }
}

fn emergencyExit(kind: []const u8, detail: []const u8) noreturn {
    emergencyWrite(kind, detail);
    linux.exit_group(if (emergency_strict) strict_failure_exit else 0);
}

pub const panic = std.debug.FullPanic(struct {
    fn f(msg: []const u8, _: ?usize) noreturn {
        emergencyExit("panic", msg);
    }
}.f);

fn onCrash(sig: linux.SIG) callconv(.c) void {
    emergencyExit("signal", switch (sig) {
        .SEGV => "SIGSEGV",
        .BUS => "SIGBUS",
        .ILL => "SIGILL",
        .FPE => "SIGFPE",
        else => "signal",
    });
}

fn installCrashHandlers() void {
    var act: linux.Sigaction = .{ .handler = .{ .handler = onCrash }, .mask = linux.sigemptyset(), .flags = 0 };
    for ([_]linux.SIG{ .SEGV, .BUS, .ILL, .FPE }) |s| _ = linux.sigaction(s, &act, null);
    // A closed stdout must not kill the prolog: writes fail instead.
    var ign: linux.Sigaction = .{ .handler = .{ .handler = linux.SIG.IGN }, .mask = linux.sigemptyset(), .flags = 0 };
    _ = linux.sigaction(.PIPE, &ign, null);
}

// ---- arguments ---------------------------------------------------------

const Args = struct {
    shell: bool = false,
    restore: bool = false,
    help: bool = false,
    version: bool = false,
    strict: bool = false,
    chain: ?[]const u8 = null,
    timeout: ?[]const u8 = null,
};

/// parse_args: documented flags only; anything else is skipped. --chain and
/// --timeout take the next word whatever it is, "" when there is none.
fn parseArgs(argv: []const []const u8) Args {
    var a: Args = .{};
    var i: usize = 0;
    while (i < argv.len) : (i += 1) {
        const arg = argv[i];
        if (std.mem.eql(u8, arg, "--shell")) {
            a.shell = true;
        } else if (std.mem.eql(u8, arg, "--strict")) {
            a.strict = true;
        } else if (std.mem.eql(u8, arg, "--restore")) {
            a.restore = true;
        } else if (std.mem.eql(u8, arg, "-h") or std.mem.eql(u8, arg, "--help")) {
            a.help = true;
        } else if (std.mem.eql(u8, arg, "--version")) {
            a.version = true;
        } else if (std.mem.eql(u8, arg, "--chain")) {
            i += 1;
            a.chain = if (i < argv.len) argv[i] else "";
        } else if (std.mem.startsWith(u8, arg, "--chain=")) {
            a.chain = arg["--chain=".len..];
        } else if (std.mem.eql(u8, arg, "--timeout")) {
            i += 1;
            a.timeout = if (i < argv.len) argv[i] else "";
        } else if (std.mem.startsWith(u8, arg, "--timeout=")) {
            a.timeout = arg["--timeout=".len..];
        }
    }
    return a;
}

fn contains(argv: []const []const u8, word: []const u8) bool {
    for (argv) |a| if (std.mem.eql(u8, a, word)) return true;
    return false;
}

/// float(text) as Python parses it: surrounding whitespace, a sign, digits
/// with single underscores between them, a fraction, an exponent, or
/// inf/infinity/nan in any case.
fn pyFloat(arena: Allocator, raw: []const u8) ?f64 {
    const s = text.pyStrip(raw);
    if (s.len == 0) return null;
    var body = s;
    var neg = false;
    if (body[0] == '+' or body[0] == '-') {
        neg = body[0] == '-';
        body = body[1..];
    }
    if (std.ascii.eqlIgnoreCase(body, "inf") or std.ascii.eqlIgnoreCase(body, "infinity"))
        return if (neg) -std.math.inf(f64) else std.math.inf(f64);
    if (std.ascii.eqlIgnoreCase(body, "nan")) return std.math.nan(f64);
    var clean: std.ArrayList(u8) = .empty;
    var i: usize = 0;
    var digits_before = false;
    var digits_after = false;
    var seen_digit_in_run = false;
    while (i < body.len and (std.ascii.isDigit(body[i]) or body[i] == '_')) : (i += 1) {
        if (body[i] == '_') {
            if (!seen_digit_in_run or i + 1 >= body.len or !std.ascii.isDigit(body[i + 1])) return null;
            continue;
        }
        seen_digit_in_run = true;
        digits_before = true;
        clean.append(arena, body[i]) catch return null;
    }
    if (i < body.len and body[i] == '.') {
        clean.append(arena, '.') catch return null;
        i += 1;
        seen_digit_in_run = false;
        while (i < body.len and (std.ascii.isDigit(body[i]) or body[i] == '_')) : (i += 1) {
            if (body[i] == '_') {
                if (!seen_digit_in_run or i + 1 >= body.len or !std.ascii.isDigit(body[i + 1])) return null;
                continue;
            }
            seen_digit_in_run = true;
            digits_after = true;
            clean.append(arena, body[i]) catch return null;
        }
    }
    if (!digits_before and !digits_after) return null;
    if (i < body.len and (body[i] == 'e' or body[i] == 'E')) {
        clean.append(arena, 'e') catch return null;
        i += 1;
        if (i < body.len and (body[i] == '+' or body[i] == '-')) {
            clean.append(arena, body[i]) catch return null;
            i += 1;
        }
        seen_digit_in_run = false;
        var exp_digits = false;
        while (i < body.len and (std.ascii.isDigit(body[i]) or body[i] == '_')) : (i += 1) {
            if (body[i] == '_') {
                if (!seen_digit_in_run or i + 1 >= body.len or !std.ascii.isDigit(body[i + 1])) return null;
                continue;
            }
            seen_digit_in_run = true;
            exp_digits = true;
            clean.append(arena, body[i]) catch return null;
        }
        if (!exp_digits) return null;
    }
    if (i != body.len) return null;
    if (clean.items[0] == '.') clean.insert(arena, 0, '0') catch return null;
    const v = std.fmt.parseFloat(f64, clean.items) catch return null;
    return if (neg) -v else v;
}

/// parse_timeout: a positive number of seconds at most TIMEOUT_MAX.
fn parseTimeout(arena: Allocator, s: []const u8) ?f64 {
    const v = pyFloat(arena, s) orelse return null;
    if (std.math.isNan(v) or v <= 0 or v > timeout_max) return null;
    return v;
}

// ---- the site configuration --------------------------------------------

const Settings = std.StringArrayHashMapUnmanaged([]const u8);

const Config = struct {
    settings: ?Settings = null,
    warning: ?[]const u8 = null,
};

fn pyStripBytes(s: []const u8) []const u8 {
    return std.mem.trim(u8, s, " \t\n\r\x0b\x0c");
}

/// parse_config_bytes: KEY = VALUE lines; '#' comments; a key that is not
/// UTF-8 is skipped; a value that is not UTF-8 is kept as its bytes and
/// named in the returned list.
fn parseConfigBytes(arena: Allocator, blob: []const u8, bad: *std.ArrayList([]const u8)) Allocator.Error!Settings {
    var settings: Settings = .empty;
    var i: usize = 0;
    while (i < blob.len) {
        var j = i;
        while (j < blob.len and blob[j] != '\n' and blob[j] != '\r') j += 1;
        const raw = blob[i..j];
        // bytes.splitlines: \r\n, \r and \n each end a line.
        i = if (j + 1 < blob.len and blob[j] == '\r' and blob[j + 1] == '\n') j + 2 else j + 1;
        const line = pyStripBytes(raw);
        if (line.len == 0 or line[0] == '#') continue;
        const eq = std.mem.indexOfScalar(u8, line, '=') orelse continue;
        const key = pyStripBytes(line[0..eq]);
        const value = pyStripBytes(line[eq + 1 ..]);
        if (!std.unicode.utf8ValidateSlice(key) or key.len == 0) continue;
        if (!std.unicode.utf8ValidateSlice(value)) try bad.append(arena, key);
        try settings.put(arena, key, value);
    }
    return settings;
}

const errno_text = [_]struct { e: linux.E, s: []const u8 }{
    .{ .e = .NOENT, .s = "No such file or directory" },
    .{ .e = .ACCES, .s = "Permission denied" },
    .{ .e = .PERM, .s = "Operation not permitted" },
    .{ .e = .LOOP, .s = "Too many levels of symbolic links" },
    .{ .e = .NOTDIR, .s = "Not a directory" },
    .{ .e = .IO, .s = "Input/output error" },
    .{ .e = .ISDIR, .s = "Is a directory" },
};

fn osErrorText(arena: Allocator, e: linux.E, path: []const u8) []const u8 {
    for (errno_text) |t| if (t.e == e)
        return std.fmt.allocPrint(arena, "[Errno {d}] {s}: '{s}'", .{ @backingInt(e), t.s, path }) catch t.s;
    return std.fmt.allocPrint(arena, "[Errno {d}] Unknown error {d}: '{s}'", .{ @backingInt(e), @backingInt(e), path }) catch "error";
}

fn lstatRaw(path: []const u8, x: *linux.Statx) linux.E {
    var zb: [sys.path_max:0]u8 = undefined;
    const z = sys.z(&zb, path) catch return .NAMETOOLONG;
    const mask: linux.STATX = .{ .TYPE = true, .MODE = true, .UID = true };
    return linux.errno(linux.statx(linux.AT.FDCWD, z.ptr, linux.AT.SYMLINK_NOFOLLOW, mask, x));
}

/// _config_untrusted: the reason a config path is not trusted, or null.
fn configUntrusted(arena: Allocator, path: []const u8) ?[]const u8 {
    var x: linux.Statx = undefined;
    const e = lstatRaw(path, &x);
    if (e != .SUCCESS) return std.fmt.allocPrint(arena, "{s} ({s})", .{ path, osErrorText(arena, e, path) }) catch path;
    const fmt_bits = @as(u32, x.mode) & fsx.S.IFMT;
    if (fmt_bits == fsx.S.IFLNK) return std.fmt.allocPrint(arena, "{s} is a symlink and is ignored", .{path}) catch path;
    if (fmt_bits != fsx.S.IFREG) return std.fmt.allocPrint(arena, "{s} is not a regular file and is ignored", .{path}) catch path;
    if (x.uid != 0) return std.fmt.allocPrint(arena, "{s} is not owned by root and is ignored", .{path}) catch path;
    if (x.mode & 0o022 != 0) return std.fmt.allocPrint(arena, "{s} is writable by group or others and is ignored", .{path}) catch path;
    return null;
}

fn exists(path: []const u8) bool {
    _ = fsx.statAt(fsx.cwd, path, true) catch return false;
    return true;
}

/// site_config: /etc/eessi-arch-guard.conf, else the file beside the
/// program as it was invoked; the first that exists decides.
fn siteConfig(arena: Allocator, invoked: []const u8) Config {
    const beside = std.fs.path.join(arena, &.{ std.fs.path.dirname(invoked) orelse "/", "eessi-arch-guard.conf" }) catch return .{};
    for ([_][]const u8{ "/etc/eessi-arch-guard.conf", beside }) |path| {
        if (!exists(path)) continue;
        if (configUntrusted(arena, path)) |reason|
            return .{ .warning = std.fmt.allocPrint(arena, "eessi-arch-guard: config ignored: {s}", .{reason}) catch null };
        // Checked again on the open file, without following a link.
        const f = fsx.openRegular(fsx.cwd, path, false) catch |err|
            return .{ .warning = std.fmt.allocPrint(arena, "eessi-arch-guard: config ignored: {s}: {t}", .{ path, err }) catch null };
        defer sys.close(f.fd);
        if (f.stat.uid != 0 or f.stat.mode & 0o022 != 0)
            return .{ .warning = std.fmt.allocPrint(arena, "eessi-arch-guard: config ignored: {s} changed while it was read", .{path}) catch null };
        const blob = fsx.readAll(arena, f.fd, 1 << 20) catch |err|
            return .{ .warning = std.fmt.allocPrint(arena, "eessi-arch-guard: config ignored: {s}: {t}", .{ path, err }) catch null };
        var bad: std.ArrayList([]const u8) = .empty;
        const settings = parseConfigBytes(arena, blob, &bad) catch return .{};
        if (bad.items.len > 0) {
            const shown = std.mem.join(arena, " ", bad.items) catch "";
            return .{ .settings = settings, .warning = std.fmt.allocPrint(arena, "eessi-arch-guard: config value kept with its original bytes: {s}", .{shown}) catch null };
        }
        return .{ .settings = settings };
    }
    return .{};
}

fn chainPathOk(path: []const u8) bool {
    if (path.len == 0 or path[0] != '/' or text.hasLineBreakOrNul(path)) return false;
    return guard.lmodInvocable(path);
}

fn resolveTimeout(arena: Allocator, cli_text: ?[]const u8, settings: ?Settings) struct { value: f64, warning: ?[]const u8 } {
    var chosen = default_timeout;
    var warnings: std.ArrayList([]const u8) = .empty;
    if (settings) |s| if (s.get("timeout")) |t| {
        if (parseTimeout(arena, t)) |v| chosen = v else warnings.append(arena, "eessi-arch-guard: config timeout is not a positive number of seconds at most " ++ timeout_max_text) catch {};
    };
    if (cli_text) |t| {
        if (parseTimeout(arena, t)) |v| chosen = v else warnings.append(arena, "eessi-arch-guard: --timeout is not a positive number of seconds at most " ++ timeout_max_text) catch {};
    }
    if (warnings.items.len == 0) return .{ .value = chosen, .warning = null };
    return .{ .value = chosen, .warning = std.mem.join(arena, " ", warnings.items) catch null };
}

fn writeMessage(arena: Allocator, fd: sys.fd_t, msg: []const u8) void {
    const b = text.messageBytes(arena, msg) catch return;
    cli.put(fd, b);
    cli.put(fd, "\n");
}

fn writeStdout(chain_out: []const u8, body: []const u8) void {
    if (chain_out.len > 0) {
        cli.put(1, chain_out);
        if (chain_out[chain_out.len - 1] != '\n') cli.put(1, "\n");
    }
    cli.put(1, body);
}

/// The path this program was started as, for the config beside it: argv[0]
/// when it names a path, else /proc/self/exe.
fn invokedPath(arena: Allocator, argv0: []const u8) []const u8 {
    if (std.mem.indexOfScalar(u8, argv0, '/') != null) {
        if (argv0[0] == '/') return argv0;
        var rb: [sys.path_max]u8 = undefined;
        const cwd = fsx.realpath(fsx.cwd, ".", &rb) catch return argv0;
        return std.fs.path.join(arena, &.{ cwd, argv0 }) catch argv0;
    }
    return selfExe(arena);
}

fn selfExe(arena: Allocator) []const u8 {
    var buf: [sys.path_max]u8 = undefined;
    const n = sys.call(linux.readlinkat, .{ linux.AT.FDCWD, "/proc/self/exe", &buf, buf.len }) catch return "/proc/self/exe";
    return arena.dupe(u8, buf[0..n]) catch "/proc/self/exe";
}

fn realMain(arena: Allocator, argv0: []const u8, argv: []const []const u8, envp: proc.Envp) u8 {
    const args = parseArgs(argv);
    const env = envmap.fromEnvp(arena, envp) catch emergencyExit("MemoryError", "out of memory");
    var ctx: guard.Ctx = .{
        .arena = arena,
        .env = env,
        .strict = emergency_strict,
        .self_exe = selfExe(arena),
    };
    if (args.restore) return guard.restoreMain(&ctx);
    if (args.help) {
        cli.put(1, help_text);
        return 0;
    }
    if (args.version) {
        cli.put(1, version ++ "\n");
        return 0;
    }

    var chain_out: []const u8 = "";
    const cfg = siteConfig(arena, invokedPath(arena, argv0));
    var notes: std.ArrayList([]const u8) = .empty;
    if (cfg.warning) |w| notes.append(arena, w) catch {};
    var chain: ?[]const u8 = null;
    if (!args.shell) {
        chain = args.chain;
        if (chain == null) {
            chain = if (cfg.settings) |s| s.get("chain") else null;
            if (chain) |ch| {
                if (ch.len == 0) {
                    chain = null;
                } else if (!chainPathOk(ch)) {
                    notes.append(arena, std.fmt.allocPrint(arena, "eessi-arch-guard: config chain is not an absolute executable: {s}", .{text.oneLine(arena, ch, 200) catch ch}) catch "") catch {};
                    chain = null;
                }
            }
        } else if (!chainPathOk(chain.?)) {
            notes.append(arena, "eessi-arch-guard: --chain is not an absolute executable") catch {};
            chain = null;
        }
    }
    for (notes.items) |n| writeMessage(arena, 2, n);
    if (chain) |path| {
        const envp_c = envmap.toEnvp(arena, env) catch envp;
        const r = proc.run(arena, path, &.{path}, envp_c, .{
            .stdout_limit = 64 << 20,
            .stderr_limit = 64 << 20,
            .timeout_ms = null,
            .new_session = false,
        }) catch |err| emergencyExit("OSError", @errorName(err));
        if (r.stderr.len > 0) cli.put(2, r.stderr);
        // A chain killed by a signal is 1, as a negative returncode is.
        const code: u8 = switch (r.term) {
            .exited => |x| x,
            .signaled => 1,
        };
        chain_out = r.stdout;
        if (code != 0) {
            writeStdout(chain_out, "");
            return code;
        }
    }

    const t = resolveTimeout(arena, args.timeout, cfg.settings);
    if (t.warning) |w| writeMessage(arena, 2, w);

    const outcome = guard.limitedDecide(&ctx, env, args.shell, t.value);
    const announce = !args.shell and std.mem.eql(u8, env.get("SLURM_LOCALID") orelse "0", "0");
    const body = envmap.render(arena, outcome.changes, outcome.message, args.shell, announce) catch |err| {
        writeStdout(chain_out, "");
        emergencyWrite(if (err == error.OutOfMemory) "MemoryError" else "ValueError", envmap.renderErrorText(err));
        return if (args.strict) strict_failure_exit else 0;
    };
    if (args.shell) if (outcome.message) |m| if (m.len > 0) writeMessage(arena, 2, m);
    if (std.mem.eql(u8, env.get("EESSI_ARCH_GUARD_VERBOSE") orelse empty, "1")) if (outcome.detail) |d| if (d.len > 0) writeMessage(arena, 2, d);
    writeStdout(chain_out, body);
    if (args.strict and outcome.kind == .failed) return strict_failure_exit;
    return 0;
}

pub fn main(init: std.process.Init.Minimal) u8 {
    var arena_state = std.heap.ArenaAllocator.init(std.heap.page_allocator);
    const arena = arena_state.allocator();
    const all = init.args.toSlice(arena) catch emergencyExit("MemoryError", "out of memory");
    const argv0: []const u8 = if (all.len > 0) all[0] else "";
    const argv = arena.alloc([]const u8, if (all.len > 0) all.len - 1 else 0) catch emergencyExit("MemoryError", "out of memory");
    for (argv, 0..) |*a, i| a.* = all[i + 1];
    emergency_shell = contains(argv, "--shell");
    emergency_strict = contains(argv, "--strict");
    installCrashHandlers();
    return realMain(arena, argv0, argv, init.environ.block.slice.ptr);
}

test {
    _ = text;
    _ = envmap;
    _ = @import("json.zig");
    _ = guard;
}

test "parseArgs" {
    const a = parseArgs(&.{ "--shell", "--chain", "--strict", "--timeout=5", "x" });
    try std.testing.expect(a.shell and !a.strict);
    try std.testing.expectEqualStrings("--strict", a.chain.?);
    try std.testing.expectEqualStrings("5", a.timeout.?);
    const b = parseArgs(&.{"--timeout"});
    try std.testing.expectEqualStrings("", b.timeout.?);
}

test "pyFloat follows Python's float()" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const a = arena_state.allocator();
    try std.testing.expectEqual(@as(?f64, 1000.0), pyFloat(a, " 1_000 "));
    try std.testing.expectEqual(@as(?f64, 0.5), pyFloat(a, ".5"));
    try std.testing.expectEqual(@as(?f64, 5.0), pyFloat(a, "5."));
    try std.testing.expectEqual(@as(?f64, 2e3), pyFloat(a, "+2E3"));
    try std.testing.expect(std.math.isInf(pyFloat(a, "-Infinity").?));
    try std.testing.expect(pyFloat(a, "1__0") == null);
    try std.testing.expect(pyFloat(a, "_1") == null);
    try std.testing.expect(pyFloat(a, "1e") == null);
    try std.testing.expect(pyFloat(a, ".") == null);
    try std.testing.expect(pyFloat(a, "0x10") == null);
    try std.testing.expect(parseTimeout(a, "inf") == null);
    try std.testing.expect(parseTimeout(a, "0") == null);
    try std.testing.expect(parseTimeout(a, "9223372037") == null);
    try std.testing.expectEqual(@as(?f64, 9223372036.0), parseTimeout(a, "9223372036"));
}

test "parseConfigBytes keeps other keys after a bad value" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const a = arena_state.allocator();
    var bad: std.ArrayList([]const u8) = .empty;
    const s = try parseConfigBytes(a, "# c\r\nchain = /bin/true\ntimeout=\xff\n \nnoeq\n=x\ntimeout = 30\r", &bad);
    try std.testing.expectEqualStrings("/bin/true", s.get("chain").?);
    try std.testing.expectEqualStrings("30", s.get("timeout").?);
    try std.testing.expectEqual(@as(usize, 1), bad.items.len);
}
