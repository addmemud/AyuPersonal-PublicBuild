"""Check the real local-history merge rule with minimal attribute doubles."""
from pathlib import Path
import subprocess
import tempfile

source = Path("source/submodules/TelegramCore/Sources/State/ServerMessageMerge.swift").read_text(encoding="utf-8")
start = source.index("func ayuPreservingArchiveHistory(")
opening = source.index("{", start)
depth = 0
for position in range(opening, len(source)):
    depth += (source[position] == "{") - (source[position] == "}")
    if depth == 0:
        logic = source[start:position + 1]
        break
else:
    raise ValueError("Unclosed declaration")

stubs = r'''
import Foundation
protocol MessageAttribute {}
final class Other: MessageAttribute {}
final class ArchivedMessageAttribute: MessageAttribute {
    let versions: [Int]
    init(_ versions: [Int]) { self.versions = versions }
}
'''
checks = r'''
let previous = ArchivedMessageAttribute([1])
let replacement = ArchivedMessageAttribute([1, 2])
let preserved = ayuPreservingArchiveHistory(previous: [previous], incoming: [Other()])
precondition(preserved.count == 2 && (preserved.last as? ArchivedMessageAttribute)?.versions == [1])
let updated = ayuPreservingArchiveHistory(previous: [previous], incoming: [replacement])
precondition(updated.count == 1 && (updated.first as? ArchivedMessageAttribute)?.versions == [1, 2])
precondition(ayuPreservingArchiveHistory(previous: [], incoming: []).isEmpty)
print("History survives refresh, updated history wins, and no history is invented")
'''
with tempfile.TemporaryDirectory() as temp:
    main = Path(temp) / "main.swift"
    main.write_text(stubs + "\n" + logic + "\n" + checks, encoding="utf-8")
    subprocess.run(["swiftc", str(main), "-o", str(Path(temp) / "test")], check=True)
    subprocess.run([str(Path(temp) / "test")], check=True)
