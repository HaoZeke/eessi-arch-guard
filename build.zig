const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{ .default_target = .{ .abi = .musl, .cpu_model = .baseline } });
    const optimize = b.standardOptimizeOption(.{ .preferred_optimize_mode = .ReleaseSafe });
    // Debug info carries absolute build paths; a release binary leaves it
    // out so two builds of one commit are byte-identical.
    const strip = b.option(bool, "strip", "Leave out debug info (default: on for release builds)") orelse (optimize != .debug);

    const options = b.addOptions();
    options.addOption([]const u8, "version", "0.2.0");

    const common = b.dependency("zig_common", .{ .target = target, .optimize = optimize });
    const mod = b.createModule(.{
        .root_source_file = b.path("zig/main.zig"),
        .target = target,
        .optimize = optimize,
        .strip = strip,
        .imports = &.{
            .{ .name = "zig_common", .module = common.module("zig_common") },
            .{ .name = "build_options", .module = options.createModule() },
        },
    });
    const exe = b.addExecutable(.{ .name = "eessi-arch-guard", .root_module = mod, .linkage = .static });
    b.installArtifact(exe);

    const unit = b.addTest(.{ .root_module = mod });
    const test_step = b.step("test", "Run the Zig unit tests");
    test_step.dependOn(&b.addRunArtifact(unit).step);

    // The Python test suite against this binary.
    const suite = b.addSystemCommand(&.{ "bash", "-c", "TOOL_BIN=\"$0\" exec python3 -m unittest discover -s tests" });
    suite.addArtifactArg(exe);
    suite.expectExitCode(0);
    b.step("suite", "Run the Python test suite against the binary").dependOn(&suite.step);
}
