#!/usr/bin/env python3
"""Restore and build the pinned Ayu source on an isolated macOS runner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import zipfile

PACKAGE = Path(__file__).resolve().parents[1]
UPSTREAM = "https://github.com/TelegramMessenger/Telegram-iOS.git"
# Official, intentionally public TEST ONLY credentials, not a leaked client key.
# https://github.com/telegramdesktop/tdesktop/blob/dev/docs/api_credentials.md
PUBLIC_TEST_API_ID = "17349"
PUBLIC_TEST_API_HASH = "344583e45741c457fe1862106095a5eb"


def run(*args: str, cwd: Path) -> None:
    subprocess.run(list(args), cwd=cwd, check=True)


def digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def unsigned_application_rule(text: str) -> str:
    """Honor upstream's no-signing feature when embedding the app profile too."""
    start = text.index("def _ios_application_impl(ctx):")
    end = text.index("\ndef ", start + 1)
    application = text[start:end]
    before = "    if platform_prerequisites.platform.is_device:\n        processor_partials.append(\n            partials.provisioning_profile_partial("
    if application.count(before) != 1:
        raise ValueError("Pinned iOS application provisioning step changed")
    after = before.replace(".is_device:", '.is_device and "disable_legacy_signing" not in features:')
    return text[:start] + application.replace(before, after, 1) + text[end:]


def prepare_unsigned_rules(source: Path) -> dict:
    # Only the disposable runner submodule, never the user's source or Apple settings.
    rules = source / "build-system/bazel-rules/rules_apple"
    expected = "485016eb5b0948cc17307064e7977a6d68e94046"
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=rules, text=True).strip()
    if actual != expected:
        raise ValueError("Unexpected rules_apple revision for unsigned build")
    relative = "apple/internal/ios_rules.bzl"
    run("git", "diff", "--exit-code", "HEAD", "--", relative, cwd=rules)
    path = rules / relative
    original_hash = digest(path)
    updated = unsigned_application_rule(path.read_text(encoding="utf-8"))
    path.write_text(updated, encoding="utf-8")
    return dict(revision=expected, file=relative, original_sha256=original_hash, patched_sha256=digest(path))


def configuration(template: dict, target: str, environment: dict, bundle_id: str) -> tuple[dict, bool]:
    if target not in ("simulator", "unsigned_iphone"):
        raise ValueError("Unknown build target")
    api_id = environment.get("AYU_TELEGRAM_API_ID", "").strip()
    api_hash = environment.get("AYU_TELEGRAM_API_HASH", "").strip()
    test_mode = environment.get("AYU_USE_PUBLIC_TEST_API", "false").strip().lower()
    if test_mode not in ("true", "false"):
        raise ValueError("AYU_USE_PUBLIC_TEST_API must be true or false")
    if test_mode == "true":
        # An explicit choice takes precedence over any repository secrets.
        api_id, api_hash = PUBLIC_TEST_API_ID, PUBLIC_TEST_API_HASH
    has_credentials = bool(api_id or api_hash)
    if has_credentials:
        if not re.fullmatch(r"[1-9][0-9]*", api_id) or int(api_id) > 2**31 - 1:
            raise ValueError("Set a valid AYU_TELEGRAM_API_ID repository secret")
        if not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
            raise ValueError("Set a valid AYU_TELEGRAM_API_HASH repository secret")
    elif target != "simulator":
        raise ValueError("An iPhone build requires your own Telegram API ID and API hash in repository secrets")
    else:
        # Compile-only constants, not Telegram's upstream API credentials.
        # This simulator artifact is not a usable logged-in client.
        api_id, api_hash = "1", "0" * 32
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+){2,}", bundle_id) or bundle_id == "ph.telegra.Telegraph":
        raise ValueError("A separate valid bundle ID is required")
    team_id = environment.get("AYU_APPLE_TEAM_ID", "").strip() or "0000000000"
    if not re.fullmatch(r"[A-Z0-9]{10}", team_id):
        raise ValueError("AYU_APPLE_TEAM_ID must contain ten uppercase letters/digits")
    config = dict(template)
    config.update(api_id=api_id, api_hash=api_hash, team_id=team_id, bundle_id=bundle_id,
                  app_specific_url_scheme="ayupersonal", is_internal_build="false",
                  is_appstore_build="false", enable_siri=False, enable_icloud=False)
    return config, has_credentials


def verify_ipa(path, target: str, bundle_id: str) -> dict:
    expected_platform = {"simulator": 7, "unsigned_iphone": 2}[target]
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("IPA CRC verification failed")
        names = set(archive.namelist())
        prefix = "Payload/Telegram.app/"
        info = plistlib.loads(archive.read(prefix + "Info.plist"))
        if info.get("CFBundleIdentifier") != bundle_id:
            raise ValueError("Unexpected bundle ID in IPA")
        if info.get("CFBundleDisplayName") != "Telegram":
            raise ValueError(f"Telegram display name is missing from IPA (found {info.get('CFBundleDisplayName')!r})")
        executable = info.get("CFBundleExecutable", "")
        if not executable or "/" in executable or "\\" in executable:
            raise ValueError("Invalid executable name in IPA")
        framework = prefix + "Frameworks/TelegramUIFramework.framework/TelegramUIFramework"
        if framework not in names:
            raise ValueError("TelegramUIFramework is missing from IPA")
        with archive.open(prefix + executable) as binary:
            header = binary.read(32)
            if len(header) != 32 or header[:4] != b"\xcf\xfa\xed\xfe":
                raise ValueError("Expected a 64-bit little-endian Mach-O executable")
            if struct.unpack_from("<I", header, 4)[0] != 0x0100000C:
                raise ValueError("Expected an arm64 executable")
            count, command_bytes = struct.unpack_from("<II", header, 16)
            if command_bytes > 16 * 1024 * 1024:
                raise ValueError("Invalid Mach-O load-command size")
            commands = binary.read(command_bytes)
        offset, binary_platform = 0, None
        for _ in range(count):
            if offset + 8 > len(commands):
                raise ValueError("Truncated Mach-O load commands")
            command, size = struct.unpack_from("<II", commands, offset)
            if size < 8 or offset + size > len(commands):
                raise ValueError("Invalid Mach-O load command")
            if command == 0x32:  # LC_BUILD_VERSION
                if size < 24:
                    raise ValueError("Truncated LC_BUILD_VERSION")
                binary_platform = struct.unpack_from("<I", commands, offset + 8)[0]
            offset += size
        if binary_platform != expected_platform:
            raise ValueError("IPA targets the wrong Apple platform (device vs simulator)")
        if target == "unsigned_iphone" and prefix + "embedded.mobileprovision" in names:
            raise ValueError("Unexpected provisioning profile in the unsigned archive")
        return {"bundle_id": bundle_id, "architecture": "arm64", "mach_o_platform": binary_platform,
                "display_name": info["CFBundleDisplayName"], "target": target}


def restore(manifest: dict, source: Path) -> None:
    patch = PACKAGE / "ayu-personal.patch"
    if manifest["upstream_url"] != UPSTREAM or not re.fullmatch(r"[0-9a-f]{40}", manifest["upstream_commit"]):
        raise ValueError("Unexpected upstream source")
    if digest(patch) != manifest["patch_sha256"]:
        raise ValueError("Source patch checksum mismatch")
    if source.exists():
        raise ValueError("Source destination already exists; choose a fresh folder")
    source.mkdir(parents=True)
    run("git", "init", cwd=source)
    run("git", "config", "core.autocrlf", "false", cwd=source)
    run("git", "remote", "add", "upstream", UPSTREAM, cwd=source)
    run("git", "fetch", "--depth", "1", "upstream", manifest["upstream_commit"], cwd=source)
    run("git", "checkout", "--detach", "FETCH_HEAD", cwd=source)
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if actual != manifest["upstream_commit"]:
        raise ValueError("Upstream commit mismatch")
    run("git", "apply", "--check", "--", str(patch), cwd=source)
    run("git", "apply", "--", str(patch), cwd=source)


def build(args) -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("This build requires an arm64 macOS runner")
    source = args.source.resolve()
    output = args.output.resolve()
    manifest = json.loads((PACKAGE / "source-manifest.json").read_text(encoding="utf-8"))
    restore(manifest, source)
    versions = json.loads((source / "versions.json").read_text())
    xcode = subprocess.check_output(["xcodebuild", "-version"], text=True).splitlines()[0]
    if xcode != "Xcode " + versions["xcode"]:
        raise ValueError("Runner Xcode does not match versions.json")
    run("xcrun", "--sdk", "iphoneos", "--show-sdk-path", cwd=source)
    template = json.loads((source / "build-system/template_minimal_development_configuration.json").read_text())
    config, has_credentials = configuration(template, args.target, os.environ, manifest["bundle_id"])
    public_test = os.environ.get("AYU_USE_PUBLIC_TEST_API", "false").strip().lower() == "true"
    credential_source = "public_test" if public_test else ("repository_secrets" if has_credentials else "compile_only")
    if public_test:
        print("TEST ONLY: public Telegram API credentials; login may be restricted by Telegram", flush=True)
    if not has_credentials:
        print("Compile-only simulator build: Telegram login is not configured", flush=True)
    config_path = source / "Ayu/local/configuration.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("x", encoding="utf-8") as file:
        os.chmod(config_path, 0o600)
        json.dump(config, file)
    run(sys.executable, "-B", "-m", "unittest", "discover", "-s", "build-system/Make/tests",
        "-p", "test_project_generation.py", "-v", cwd=source)
    run(sys.executable, "-B", "build-system/Make/PostboxImportCheck.py", cwd=source)
    run(sys.executable, "-B", "-m", "unittest", "discover", "-s", "Ayu/Tests",
        "-p", "test_project_tools.py", "-v", cwd=source)
    run(sys.executable, "-B", "Ayu/tools/project.py", "test-policy", cwd=source)
    run("git", "submodule", "sync", "--recursive", cwd=source)
    run("git", "submodule", "update", "--init", "--recursive", "--jobs", "4", cwd=source)
    # Only the fresh, disposable runner checkout is changed. No global Bazel settings.
    flags = ["--//Telegram:disableExtensions", "--//Telegram:disableProvisioningProfiles", "--jobs=2"]
    unsigned_rules = None
    if args.target == "unsigned_iphone":
        unsigned_rules = prepare_unsigned_rules(source)
        flags.append("--features=disable_legacy_signing")
    with (source / ".bazelrc").open("a", encoding="utf-8") as file:
        file.write("\n# Ayu isolated cloud build\n")
        file.writelines("build " + flag + "\n" for flag in flags)
    build_number = os.environ.get("GITHUB_RUN_NUMBER", "1")
    if not re.fullmatch(r"[1-9][0-9]*", build_number):
        raise ValueError("Invalid build number")
    run(sys.executable, "-u", "-B", "build-system/Make/Make.py", "--cacheDir", str(source.parent / "bazel-cache"),
        "build", "--configurationPath", str(config_path), "--xcodeManagedCodesigning",
        "--configuration", "debug_sim_arm64" if args.target == "simulator" else "release_arm64",
        "--buildNumber", build_number, "--continueOnError", cwd=source)
    ipa = source / "bazel-bin/Telegram/Telegram.ipa"
    report = dict(upstream_commit=manifest["upstream_commit"], patch_sha256=manifest["patch_sha256"],
                  api_credentials_configured=has_credentials, build_number=build_number,
                  credential_source=credential_source, test_only=public_test,
                  unsigned_rules_adjustment=unsigned_rules,
                  runtime_verified=False, xcode=versions["xcode"], bazel=versions["bazel"],
                  build_flags=flags, ipa_sha256=digest(ipa))
    output.mkdir(parents=True, exist_ok=True)
    name = "Ayu-Personal-simulator.ipa" if args.target == "simulator" else "Ayu-Personal-unsigned-iphone.ipa"
    if public_test:
        name = name.replace(".ipa", "-public-test.ipa")
    try:
        report.update(verify_ipa(ipa, args.target, manifest["bundle_id"]), verification="passed")
    except (ValueError, KeyError, zipfile.BadZipFile) as error:
        report.update(verification="failed", target=args.target, error=str(error))
        shutil.copyfile(ipa, output / name.replace(".ipa", "-unverified.ipa"))
        (output / "build-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        raise
    shutil.copyfile(ipa, output / name)
    (output / "build-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Built and verified:", name, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("simulator", "unsigned_iphone"), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    try:
        build(parser.parse_args())
    except (ValueError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile, KeyError) as error:
        print("Build failed:", error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
