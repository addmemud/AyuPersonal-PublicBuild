import importlib.util
import io
from pathlib import Path
import plistlib
import struct
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location("ayu_ci", Path(__file__).resolve().parents[1] / "tools/ci.py")
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)
BUNDLE = "org.ayupersonal.addmemud"


def archive(binary_platform=7, bundle=BUNDLE, framework=True, display_name="Telegram"):
    stream = io.BytesIO()
    info = {"CFBundleIdentifier": bundle, "CFBundleDisplayName": display_name, "CFBundleExecutable": "Telegram"}
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 1, 24, 0, 0)
    command = struct.pack("<IIIIII", 0x32, 24, binary_platform, 0, 0, 0)
    with zipfile.ZipFile(stream, "w") as file:
        file.writestr("Payload/Telegram.app/Info.plist", plistlib.dumps(info))
        file.writestr("Payload/Telegram.app/Telegram", header + command)
        if framework:
            file.writestr("Payload/Telegram.app/Frameworks/TelegramUIFramework.framework/TelegramUIFramework", b"fixture")
    stream.seek(0)
    return stream


class ConfigTests(unittest.TestCase):
    def test_unsigned_rule_change_is_scoped_to_application(self):
        step = '    if platform_prerequisites.platform.is_device:\n        processor_partials.append(\n            partials.provisioning_profile_partial(\n'
        original = 'HEADER\ndef _ios_application_impl(ctx):\n' + step + '\ndef another_rule(ctx):\n' + step
        updated = ci.unsigned_application_rule(original)
        self.assertIn('and "disable_legacy_signing" not in features:', updated)
        self.assertEqual(updated.split('\ndef another_rule')[1], original.split('\ndef another_rule')[1])
        self.assertEqual(updated.count('and "disable_legacy_signing"'), 1)

    def test_unsigned_rule_rejects_unknown_source_layout(self):
        with self.assertRaises(ValueError):
            ci.unsigned_application_rule('def _ios_application_impl(ctx):\n    pass\ndef next():\n    pass\n')

    def test_public_test_api_is_explicit_and_works_for_both_targets(self):
        for target in ("simulator", "unsigned_iphone"):
            config, ready = ci.configuration({}, target, {"AYU_USE_PUBLIC_TEST_API": "true"}, BUNDLE)
            self.assertTrue(ready)
            self.assertEqual(config["api_id"], "17349")
            self.assertEqual(config["api_hash"], "344583e45741c457fe1862106095a5eb")

    def test_explicit_public_mode_does_not_use_private_secrets(self):
        env = {"AYU_USE_PUBLIC_TEST_API": "true", "AYU_TELEGRAM_API_ID": "12345",
               "AYU_TELEGRAM_API_HASH": "a" * 32}
        config, _ = ci.configuration({}, "unsigned_iphone", env, BUNDLE)
        self.assertEqual(config["api_id"], ci.PUBLIC_TEST_API_ID)

    def test_public_mode_rejects_ambiguous_value(self):
        with self.assertRaises(ValueError):
            ci.configuration({}, "unsigned_iphone", {"AYU_USE_PUBLIC_TEST_API": "yes"}, BUNDLE)

    def test_simulator_without_keys_is_explicitly_compile_only(self):
        config, ready = ci.configuration({}, "simulator", {}, BUNDLE)
        self.assertFalse(ready)
        self.assertEqual(config["api_hash"], "0" * 32)
        self.assertEqual(config["bundle_id"], BUNDLE)

    def test_device_rejects_absent_or_incomplete_credentials(self):
        for env in ({}, {"AYU_TELEGRAM_API_ID": "12345"}, {"AYU_TELEGRAM_API_HASH": "a" * 32}):
            with self.subTest(env=sorted(env)), self.assertRaises(ValueError):
                ci.configuration({}, "unsigned_iphone", env, BUNDLE)

    def test_device_preserves_supplied_credentials(self):
        env = {"AYU_TELEGRAM_API_ID": "12345", "AYU_TELEGRAM_API_HASH": "a" * 32}
        config, ready = ci.configuration({}, "unsigned_iphone", env, BUNDLE)
        self.assertTrue(ready)
        self.assertEqual(config["api_id"], "12345")
        self.assertFalse(config["enable_icloud"])

    def test_config_rejects_injection_and_official_bundle(self):
        for bundle in ('org.app.unsafe"\n', "ph.telegra.Telegraph"):
            with self.subTest(bundle=bundle), self.assertRaises(ValueError):
                ci.configuration({}, "simulator", {}, bundle)
        with self.assertRaises(ValueError):
            ci.configuration({}, "simulator", {"AYU_APPLE_TEAM_ID": 'invalid"'}, BUNDLE)


class ArtifactTests(unittest.TestCase):
    def test_accepts_matching_platform_and_identity(self):
        self.assertEqual(ci.verify_ipa(archive(), "simulator", BUNDLE)["mach_o_platform"], 7)
        self.assertEqual(ci.verify_ipa(archive(2), "unsigned_iphone", BUNDLE)["mach_o_platform"], 2)

    def test_rejects_simulator_archive_as_device_build(self):
        with self.assertRaisesRegex(ValueError, "wrong Apple platform"):
            ci.verify_ipa(archive(), "unsigned_iphone", BUNDLE)

    def test_rejects_old_app_name(self):
        with self.assertRaisesRegex(ValueError, "found 'Ayu Personal'"):
            ci.verify_ipa(archive(display_name="Ayu Personal"), "simulator", BUNDLE)

    def test_rejects_incomplete_or_wrong_app(self):
        for file in (archive(bundle="org.wrong.app"), archive(framework=False)):
            with self.assertRaises(ValueError):
                ci.verify_ipa(file, "simulator", BUNDLE)

    def test_rejects_corrupt_archive(self):
        with self.assertRaises(zipfile.BadZipFile):
            ci.verify_ipa(io.BytesIO(b"not a zip archive"), "simulator", BUNDLE)


if __name__ == "__main__":
    unittest.main()
