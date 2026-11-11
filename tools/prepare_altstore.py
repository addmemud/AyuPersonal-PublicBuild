"""Restore discoverable App Group entitlements without recompiling the app.

AltStore reads app.entitlements to request App Groups, then replaces this ad-hoc
signature with the user's personal signature. No Apple identity is used here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import plistlib
import re
import struct
import subprocess
import zipfile

BUNDLE = "org.ayupersonal.addmemud"
GROUP = "group." + BUNDLE


def requested_groups(resigned_bundle_base=""):
    groups = [GROUP]
    if resigned_bundle_base:
        if len(resigned_bundle_base) > 255 or not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", resigned_bundle_base):
            raise ValueError("Invalid AltStore bundle base")
        alias = "group." + resigned_bundle_base
        if alias not in groups:
            groups.append(alias)
    return groups


def inspect_macho(data):
    if len(data) < 32 or data[:4] != b"\xcf\xfa\xed\xfe":
        raise ValueError("Expected thin 64-bit Mach-O")
    if struct.unpack_from("<I", data, 4)[0] != 0x0100000C:
        raise ValueError("Expected arm64")
    count, size = struct.unpack_from("<II", data, 16)
    end, offset = 32 + size, 32
    if end > len(data):
        raise ValueError("Truncated load commands")
    binary_platform, text_hash, signature = None, None, False
    for _ in range(count):
        if offset + 8 > end:
            raise ValueError("Truncated command")
        command, length = struct.unpack_from("<II", data, offset)
        if length < 8 or offset + length > end:
            raise ValueError("Invalid command length")
        if command == 0x32:
            if length < 24:
                raise ValueError("Truncated platform command")
            binary_platform = struct.unpack_from("<I", data, offset + 8)[0]
        if command == 0x1D:
            signature = True
        if command == 0x19:
            if length < 72:
                raise ValueError("Truncated segment")
            sections = struct.unpack_from("<I", data, offset + 64)[0]
            if 72 + sections * 80 > length:
                raise ValueError("Truncated sections")
            for i in range(sections):
                section = offset + 72 + i * 80
                if data[section:section + 16].rstrip(b"\0") == b"__text":
                    code_size = struct.unpack_from("<Q", data, section + 40)[0]
                    code_offset = struct.unpack_from("<I", data, section + 48)[0]
                    if code_offset + code_size > len(data):
                        raise ValueError("Truncated code section")
                    text_hash = hashlib.sha256(data[code_offset:code_offset + code_size]).hexdigest()
        offset += length
    if binary_platform != 2 or text_hash is None:
        raise ValueError("Missing iPhone platform or code section")
    return dict(platform=binary_platform, text_sha256=text_hash, has_signature=signature)


def run(*args):
    subprocess.run(list(map(str, args)), check=True)


def source_artifact_id(source_run, source_artifact):
    if not re.fullmatch(r"[1-9][0-9]*", source_run):
        raise ValueError("Invalid source run ID")
    if source_artifact == "same-run":
        return None
    if not re.fullmatch(r"[1-9][0-9]*", source_artifact):
        raise ValueError("Invalid source artifact ID")
    return int(source_artifact)


def validate_source_report(report, actual_sha, expected_sha, expected_patch_sha):
    """Require independently selected checksums and the build's own device report."""
    for value in (actual_sha, expected_sha, expected_patch_sha):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Expected lowercase SHA-256 values")
    if actual_sha != expected_sha or report.get("ipa_sha256") != actual_sha:
        raise ValueError("Source IPA checksum mismatch")
    if report.get("patch_sha256") != expected_patch_sha:
        raise ValueError("Source patch checksum mismatch")
    if (report.get("credential_source") != "public_test" or report.get("test_only") is not True
            or report.get("runtime_verified") is not False or report.get("verification") != "passed"
            or report.get("target") != "unsigned_iphone" or report.get("bundle_id") != BUNDLE
            or report.get("architecture") != "arm64" or report.get("mach_o_platform") != 2
            or report.get("display_name") != "Telegram"):
        raise ValueError("Unexpected or unverified source build report")


def prepare(input_dir, output, work, source_run, source_artifact, expected_sha, expected_patch_sha, resigned_bundle_base=""):
    from ci import digest, verify_ipa
    if platform.system() != "Darwin":
        raise ValueError("Apple codesign requires macOS")
    source = input_dir / "Ayu-Personal-unsigned-iphone-public-test.ipa"
    source_artifact_value = source_artifact_id(source_run, source_artifact)
    actual_sha = digest(source)
    original_report = json.loads((input_dir / "build-report.json").read_text())
    validate_source_report(original_report, actual_sha, expected_sha, expected_patch_sha)
    verify_ipa(source, "unsigned_iphone", BUNDLE)
    if work.exists() or output.exists():
        raise ValueError("Use fresh staging and output folders")
    work.mkdir(parents=True)
    output.mkdir(parents=True)
    run("ditto", "-x", "-k", source, work)
    app = work / "Payload/Telegram.app"
    info = plistlib.loads((app / "Info.plist").read_bytes())
    main = app / info["CFBundleExecutable"]
    frameworks = sorted((app / "Frameworks").glob("*.framework"))
    binaries = [main] + [p / plistlib.loads((p / "Info.plist").read_bytes())["CFBundleExecutable"] for p in frameworks]
    before, names = {}, set()
    for binary in binaries:
        data = binary.read_bytes()
        before[str(binary.relative_to(app))] = inspect_macho(data)
        for name in ("Application", "AppDelegate", "SceneDelegate"):
            if name.encode() + b"\0" in data:
                names.add(name)
        dependencies = subprocess.check_output(["otool", "-L", str(binary)], text=True)
        for line in dependencies.splitlines()[1:]:
            dependency = line.strip().split(" ", 1)[0]
            if dependency.startswith("@rpath/") and ".framework/" in dependency:
                if not (app / "Frameworks" / dependency.removeprefix("@rpath/")).is_file():
                    raise ValueError("Missing framework: " + dependency)
    if names != {"Application", "AppDelegate", "SceneDelegate"}:
        raise ValueError("Startup class names missing from binaries")
    # AltStore can preserve a previously assigned (different) bundle identifier.
    # Request its matching group as well; AltStore appends the user's team ID.
    expected_entitlements = {"com.apple.security.application-groups": requested_groups(resigned_bundle_base)}
    entitlements_path = work / "altstore.entitlements"
    entitlements_path.write_bytes(plistlib.dumps(expected_entitlements))
    for framework in frameworks:
        run("codesign", "--force", "--sign", "-", "--timestamp=none", framework)
    run("codesign", "--force", "--sign", "-", "--timestamp=none", "--entitlements", entitlements_path, app)
    run("codesign", "--verify", "--deep", "--strict", app)
    entitlements = plistlib.loads(subprocess.check_output(["codesign", "-d", "--entitlements", ":-", str(app)]))
    if entitlements != expected_entitlements:
        raise ValueError("Unexpected signed entitlements")
    after = {str(p.relative_to(app)): inspect_macho(p.read_bytes()) for p in binaries}
    for name in before:
        if before[name]["text_sha256"] != after[name]["text_sha256"] or not after[name]["has_signature"]:
            raise ValueError("Code changed or signature missing: " + name)
    result = output / "Ayu-Personal-AltStore-public-test.ipa"
    run("ditto", "-c", "-k", "--norsrc", "--keepParent", work / "Payload", result)
    verified = verify_ipa(result, "unsigned_iphone", BUNDLE)
    report = dict(source_run=int(source_run), source_artifact=source_artifact_value,
                  source_artifact_origin="same-run-before-upload" if source_artifact_value is None else "downloaded-artifact",
                  source_ipa_sha256=actual_sha,
                  ipa_sha256=digest(result), entitlements=entitlements,
                  code_sections_unchanged=True, macho_checks=after,
                  resigned_bundle_base=resigned_bundle_base,
                  framework_dependencies_verified=True, startup_class_names=sorted(names),
                  ad_hoc_only=True, requires_personal_signing=True,
                  credential_source="public_test", test_only=True, runtime_verified=False,
                  verification=verified, source_build=original_report)
    (output / "altstore-packaging-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"result": result.name, "ipa_sha256": report["ipa_sha256"], "code_sections_unchanged": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--source-run", required=True, help="GitHub Actions run ID for this artifact")
    parser.add_argument("--source-artifact", required=True, help="GitHub Actions artifact ID")
    parser.add_argument("--source-ipa-sha256", required=True, help="Independently verified input IPA SHA-256")
    parser.add_argument("--source-patch-sha256", required=True, help="Expected source patch SHA-256")
    parser.add_argument("--resigned-bundle-base", default="")
    args = parser.parse_args()
    prepare(args.input.resolve(), args.output.resolve(), args.work.resolve(), args.source_run, args.source_artifact,
            args.source_ipa_sha256, args.source_patch_sha256, args.resigned_bundle_base)
