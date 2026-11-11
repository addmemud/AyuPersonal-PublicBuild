"""Check Ayu settings calls against the pinned ItemListUI initializer labels.

This is a lightweight guard for APIs that a syntax-only Swift parse cannot
validate. It does not replace a full Swift typecheck or an iOS UI test.
"""
from pathlib import Path
import re
import sys


def arguments(text, opening):
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = [")"]
    pieces = []
    start = opening + 1
    i = start
    quoted = False
    while i < len(text):
        char = text[i]
        if quoted:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif char in pairs:
            stack.append(pairs[char])
        elif char == stack[-1]:
            stack.pop()
            if not stack:
                if text[start:i].strip():
                    pieces.append(text[start:i])
                return pieces
        elif char == "," and len(stack) == 1:
            if text[start:i].strip():
                pieces.append(text[start:i])
            start = i + 1
        i += 1
    raise ValueError("Unclosed argument list")


def labels(parts):
    result = []
    for part in parts:
        match = re.match(r"\s*([A-Za-z_]\w*)\s*:", part)
        if not match:
            raise ValueError("Could not identify argument label: " + part[:80])
        result.append(match.group(1))
    return result


def check_order(called, allowed):
    position = 0
    for name in called:
        try:
            position = allowed.index(name, position) + 1
        except ValueError:
            return False
    return True


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "madgram-compat-check"
    specs = {
        "ItemListSwitchItem": root / "submodules/ItemListUI/Sources/Items/ItemListSwitchItem.swift",
        "ItemListDisclosureItem": root / "submodules/ItemListUI/Sources/Items/ItemListDisclosureItem.swift",
    }
    controllers = [
        root / "submodules/SettingsUI/Sources/GhostModeSettingsController.swift",
        root / "submodules/SettingsUI/Sources/Data and Storage/LocalMessageArchiveSettingsController.swift",
    ]
    allowed = {}
    for name, path in specs.items():
        content = path.read_text(encoding="utf-8")
        start = content.index("public class " + name + ":")
        initializer = content.index("public init(", start)
        allowed[name] = labels(arguments(content, content.index("(", initializer)))
    if check_order(["presentationData", "title", "titleBadgeAction"], allowed["ItemListSwitchItem"]):
        raise ValueError("Guard failed to reject unsupported switch argument")
    if check_order(["label", "title"], allowed["ItemListDisclosureItem"]):
        raise ValueError("Guard failed to reject out-of-order arguments")

    count = 0
    for path in controllers:
        content = path.read_text(encoding="utf-8")
        for name in specs:
            for match in re.finditer(r"\b" + name + r"\s*\(", content):
                called = labels(arguments(content, content.index("(", match.start())))
                if not check_order(called, allowed[name]):
                    line = content.count("\n", 0, match.start()) + 1
                    raise ValueError(f"{path}:{line}: incompatible {name} arguments: {called}")
                count += 1
    if count == 0:
        raise ValueError("No settings constructors found")
    print(f"Checked {count} settings constructors against pinned ItemListUI labels")


if __name__ == "__main__":
    main()
