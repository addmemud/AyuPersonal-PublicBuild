"""Exercise the real per-chat journal query with lightweight Postbox doubles."""
from pathlib import Path
import subprocess
import tempfile

source = Path("source/submodules/TelegramCore/Sources/TelegramEngine/Messages/DeletedMessageArchive.swift").read_text(encoding="utf-8")
start = source.index("func _internal_deletedMessagesForPeer(")
opening = source.index("{", start)
depth = 0
for pos in range(opening, len(source)):
    depth += (source[pos] == "{") - (source[pos] == "}")
    if depth == 0:
        logic = source[start:pos + 1]
        break
else:
    raise ValueError("Unclosed declaration")

stubs = r'''
import Foundation
typealias PeerId = Int
struct MessageId: Hashable { let peerId: PeerId; let namespace: Int; let id: Int }
enum Namespaces { enum Message { static let Archived = 8; static let ArchivedVersion = 9 } }
struct Attribute { let deletedAt: Int?; let pendingBurn: Bool }
struct Message { let archivedMessageAttribute: Attribute?; let timestamp: Int }
struct Entry { let messageId: MessageId }
struct DeletedMessageArchiveIndex { let messageIds: [Entry] }
enum PreferencesKeys { static let deletedMessageArchiveIndex = 1 }
struct Stored { let index: DeletedMessageArchiveIndex; func get<T>(_ type: T.Type) -> T? { index as? T } }
struct Transaction {
    let index: DeletedMessageArchiveIndex?
    let messages: [MessageId: Message]
    func getPreferencesEntry(key: Int) -> Stored? { index.map { Stored(index: $0) } }
    func getMessage(_ id: MessageId) -> Message? { messages[id] }
}
'''
checks = r'''
func id(_ peer: Int, _ space: Int, _ number: Int) -> MessageId { MessageId(peerId: peer, namespace: space, id: number) }
let a = id(1, 8, 1), b = id(1, 8, 2), foreign = id(2, 8, 3), pending = id(1, 8, 4)
let edit = id(1, 9, 5), missing = id(1, 8, 6), live = id(1, 8, 7)
let index = DeletedMessageArchiveIndex(messageIds: [a, foreign, edit, pending, b, missing, live].map { Entry(messageId: $0) })
let deleted = Attribute(deletedAt: 5, pendingBurn: false)
let tx = Transaction(index: index, messages: [a: Message(archivedMessageAttribute: deleted, timestamp: 10), b: Message(archivedMessageAttribute: deleted, timestamp: 30), foreign: Message(archivedMessageAttribute: deleted, timestamp: 100), edit: Message(archivedMessageAttribute: deleted, timestamp: 50), pending: Message(archivedMessageAttribute: Attribute(deletedAt: 5, pendingBurn: true), timestamp: 80), live: Message(archivedMessageAttribute: Attribute(deletedAt: nil, pendingBurn: false), timestamp: 90)])
precondition(_internal_deletedMessagesForPeer(transaction: tx, peerId: 1).map(\.timestamp) == [30, 10])
precondition(_internal_deletedMessagesForPeer(transaction: tx, peerId: 2).map(\.timestamp) == [100])
precondition(_internal_deletedMessagesForPeer(transaction: Transaction(index: nil, messages: [:]), peerId: 1).isEmpty)
print("Per-chat journal isolates peers, excludes edits and pending burns, and sorts deleted messages")
'''
with tempfile.TemporaryDirectory() as temp:
    file = Path(temp) / "main.swift"
    file.write_text(stubs + "\n" + logic + "\n" + checks, encoding="utf-8")
    subprocess.run(["swiftc", str(file), "-o", str(Path(temp) / "test")], check=True)
    subprocess.run([str(Path(temp) / "test")], check=True)
