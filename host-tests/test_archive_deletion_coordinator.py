"""Exercise real deletion coordination using synchronous Postbox/file doubles.

This is not an iOS, concurrency, or real filesystem integration test. The real
file store and journal primitives are checked separately by the preview suite.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path("source")
privacy = root / "submodules/TelegramCore/Sources/AyuPrivacy"


def declaration(path, start):
    text = path.read_text(encoding="utf-8")
    index = text.index(start)
    opening = text.index("{", index)
    depth = 0
    for i in range(opening, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[index:i + 1]
    raise ValueError("Unclosed declaration: " + start)


logic = "\n\n".join([
    declaration(privacy / "AyuArchiveJournalStorage.swift", "func ayuArchiveJournal("),
    declaration(privacy / "AyuArchiveJournalStorage.swift", "func updateAyuArchiveJournal("),
    declaration(privacy / "AyuArchiveSnapshotPreparation.swift", "func ayuFinishArchiveFileDeletions("),
])

stubs = r'''
import Foundation
enum NoError: Error {}
struct Signal<Value, Failure> {
    let value: Value
    static func single(_ value: Value) -> Signal<Value, Failure> { Signal(value: value) }
}
infix operator |>: AdditionPrecedence
func |> <T, U>(value: T, transform: (T) -> U) -> U { transform(value) }
final class Queue { static func concurrentDefaultQueue() -> Queue { Queue() } }
func deliverOn<T, E>(_ queue: Queue) -> (Signal<T, E>) -> Signal<T, E> { { $0 } }
func mapToSignal<T, U, E>(_ f: @escaping (T) -> Signal<U, E>) -> (Signal<T, E>) -> Signal<U, E> { { f($0.value) } }
func map<T, U, E>(_ f: @escaping (T) -> U) -> (Signal<T, E>) -> Signal<U, E> { { .single(f($0.value)) } }
enum Events { static var values: [String] = [] }
struct PreferencesEntry {
    private let data: Data
    init?<T: Encodable>(_ value: T) {
        guard let data = try? JSONEncoder().encode(value) else { return nil }
        self.data = data
    }
    func get<T: Decodable>(_ type: T.Type) -> T? { try? JSONDecoder().decode(type, from: data) }
}
enum PreferencesKeys { static let ayuArchiveJournal = "journal" }
final class Transaction {
    var entry: PreferencesEntry?
    func getPreferencesEntry(key: String) -> PreferencesEntry? { entry }
    func updatePreferencesEntry(key: String, _ f: (PreferencesEntry?) -> PreferencesEntry?) { entry = f(entry) }
}
final class MediaBox { let basePath = "unused" }
final class Postbox {
    let tx = Transaction()
    let mediaBox = MediaBox()
    func transaction<T>(_ f: (Transaction) -> T) -> Signal<T, NoError> {
        Events.values.append("transaction")
        return .single(f(tx))
    }
}
enum AyuArchiveFileStore {
    static var failed = Set<Int64>()
    static func removeAbandonedReservationFiles(mediaBoxBasePath: String, fileId: Int64, token: UUID) -> Bool {
        Events.values.append("unlink-\(fileId)")
        return !failed.contains(fileId)
    }
}
'''

tests = r'''
let first = UUID(uuidString: "00000000-0000-0000-0000-000000000001")!
let second = UUID(uuidString: "00000000-0000-0000-0000-000000000002")!
let wrong = UUID(uuidString: "00000000-0000-0000-0000-000000000003")!
let identity1 = AyuArchiveJournal.MessageIdentity(peerId: 1, namespace: 2, id: 3)
let identity2 = AyuArchiveJournal.MessageIdentity(peerId: 1, namespace: 2, id: 4)
let hash = Data(repeating: 1, count: 32)
let postbox = Postbox()
var journal = AyuArchiveJournal()
precondition(journal.reserve(fileId: 10, token: first, message: identity1, bytes: 10, createdAt: 0, limitBytes: 100))
precondition(journal.commit(fileId: 10, token: first, actualBytes: 10, sha256: hash, sourceResourceId: "a"))
precondition(journal.reserve(fileId: 11, token: second, message: identity2, bytes: 20, createdAt: 0, limitBytes: 100))
precondition(journal.commit(fileId: 11, token: second, actualBytes: 20, sha256: hash, sourceResourceId: "b"))
precondition(journal.markDeleting(fileId: 10, token: first))
precondition(journal.markDeleting(fileId: 11, token: second))
postbox.tx.entry = PreferencesEntry(journal)!
let a = AyuArchiveJournal.Deletion(fileId: 10, token: first)
let b = AyuArchiveJournal.Deletion(fileId: 11, token: second)
let bad = AyuArchiveJournal.Deletion(fileId: 10, token: wrong)
precondition(ayuFinishArchiveFileDeletions(postbox: postbox, requested: [bad]).value)
precondition(Events.values == ["transaction"])
precondition(ayuArchiveJournal(transaction: postbox.tx)!.accountedBytes == 30)
Events.values = []
AyuArchiveFileStore.failed = [11]
precondition(!ayuFinishArchiveFileDeletions(postbox: postbox, requested: [a, b]).value)
precondition(Events.values == ["transaction", "unlink-10", "unlink-11", "transaction"])
var remaining = ayuArchiveJournal(transaction: postbox.tx)!
precondition(remaining.accountedBytes == 20 && remaining.entries.count == 1 && remaining.entries[0].state == .deleting)
precondition(!remaining.reserve(fileId: 10, token: wrong, message: identity1, bytes: 1, createdAt: 0, limitBytes: 100))
AyuArchiveFileStore.failed = []
precondition(ayuFinishArchiveFileDeletions(postbox: postbox, requested: [b]).value)
remaining = ayuArchiveJournal(transaction: postbox.tx)!
precondition(remaining.entries.isEmpty && remaining.accountedBytes == 0)
print("Archive deletion coordinator checks passed with synchronous doubles")
'''

with tempfile.TemporaryDirectory(prefix="ayu-delete-coordinator-") as directory:
    directory = Path(directory)
    main = directory / "main.swift"
    main.write_text(stubs + "\n\n" + logic + "\n\n" + tests, encoding="utf-8")
    binary = directory / "archive-deletion"
    subprocess.run(["swiftc", "-warnings-as-errors", str(privacy / "AyuArchiveJournal.swift"), str(main), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
