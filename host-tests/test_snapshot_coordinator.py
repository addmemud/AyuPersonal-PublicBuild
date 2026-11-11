"""Exercise the real offline snapshot coordinator with synchronous doubles.

No real Postbox, Signal scheduling, network, or iPhone playback is involved.
File copy/hash/unlink are simulated here and tested separately in storage tests.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path('source')
source = root / 'submodules/TelegramCore/Sources/AyuPrivacy/AyuArchiveSnapshotPreparation.swift'
text = source.read_text(encoding='utf-8')


def declaration(start):
    position = text.index(start)
    opening = text.index('{', position)
    depth = 0
    for i in range(opening, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[position:i + 1]
    raise ValueError(f'Unclosed declaration: {start}')


production = '\n\n'.join(declaration(name) for name in (
    'struct AyuArchiveSnapshotCandidate',
    'struct AyuVerifiedArchiveSnapshot',
    'struct AyuBurnSnapshotReference:',
    'struct AyuVerifiedBurnSnapshot',
    'func ayuViewOnceSourceResourceId(',
    'private func ayuAbandonViewOnceReservation(',
    'func ayuPrepareViewOnceSnapshot(',
))

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
func afterNext<T, E>(_ f: @escaping (T) -> Void) -> (Signal<T, E>) -> Signal<T, E> { { signal in f(signal.value); return signal } }
var activeTestTokens = Set<UUID>()
func ayuRegisterArchiveReservation(_ token: UUID) { let _ = activeTestTokens.insert(token) }
func ayuReleaseArchiveReservation(_ token: UUID) { let _ = activeTestTokens.remove(token) }

enum Events { static var values: [String] = [] }
enum Controls {
    static var failCopy = false
    static var failHash = false
    static var failCommit = false
    static var failUnlink = false
}
enum Namespaces {
    enum Peer { static let CloudUser: Int32 = 1 }
    enum Message { static let Cloud: Int32 = 2; static let Archived: Int32 = 3 }
}
struct PeerId: Equatable { let namespace: Int32 }
struct MessageId: Equatable { let peerId: PeerId; let namespace: Int32; let id: Int32 }
struct MediaResourceId: Equatable { let stringRepresentation: String }
protocol Media {}
final class TelegramMediaResource {
    let id: MediaResourceId
    init(_ id: String) { self.id = MediaResourceId(stringRepresentation: id) }
}
struct ImageRepresentation { let resource: TelegramMediaResource }
func largestImageRepresentation(_ items: [ImageRepresentation]) -> ImageRepresentation? { items.first }
final class TelegramMediaImage: Media {
    let representations: [ImageRepresentation]
    init(_ resource: TelegramMediaResource) { representations = [ImageRepresentation(resource: resource)] }
}
final class TelegramMediaFile: Media {
    let resource: TelegramMediaResource
    init(_ resource: TelegramMediaResource) { self.resource = resource }
}
final class Message { var media: [Media]; init(_ media: [Media]) { self.media = media } }
final class MediaBox {
    let basePath: String
    let sourcePath: String
    init(_ root: String, sourcePath: String) { basePath = root; self.sourcePath = sourcePath }
    func completedResourcePath(id: MediaResourceId) -> String? { sourcePath }
}
struct AyuArchiveJournal {
    enum State { case reserved, committed, deleting }
    var state: State?
    mutating func abandonReservation(fileId: Int64, token: UUID) -> Bool {
        Events.values.append("abandon")
        guard state == .reserved else { return false }
        state = .deleting
        return true
    }
    mutating func confirmAbandonedReservationDeleted(fileId: Int64, token: UUID) -> Bool {
        Events.values.append("confirm-cleanup")
        guard state == .deleting else { return false }
        state = nil
        return true
    }
}
final class Transaction {
    let messageId: MessageId
    let message: Message
    var journal = AyuArchiveJournal()
    var reference: AyuBurnSnapshotReference?
    init(_ id: MessageId, _ message: Message) { messageId = id; self.message = message }
    func getMessage(_ id: MessageId) -> Message? { id == messageId ? message : nil }
}
final class Postbox {
    let mediaBox: MediaBox
    let tx: Transaction
    init(_ mediaBox: MediaBox, _ tx: Transaction) { self.mediaBox = mediaBox; self.tx = tx }
    func transaction<T>(_ f: (Transaction) -> T) -> Signal<T, NoError> { .single(f(tx)) }
}
final class Account { let postbox: Postbox; init(_ postbox: Postbox) { self.postbox = postbox } }
enum AyuArchiveFileStore {
    static func matches(mediaBoxBasePath: String, fileId: Int64, byteCount: Int64, sha256: Data) -> Bool {
        Events.values.append("verify")
        return !Controls.failHash
    }
    static func removeAbandonedReservationFiles(mediaBoxBasePath: String, fileId: Int64, token: UUID) -> Bool {
        Events.values.append("unlink")
        return !Controls.failUnlink
    }
}
func updateAyuArchiveJournal(transaction: Transaction, _ f: (inout AyuArchiveJournal) -> Bool) -> Bool {
    f(&transaction.journal)
}
func ayuReserveViewOnceCopy(transaction: Transaction, mediaBox: MediaBox, messageId: MessageId, fileId: Int64, token: UUID, expectedBytes: Int64, createdAt: Int64) -> Bool {
    Events.values.append("reserve")
    guard transaction.journal.state == nil else { return false }
    transaction.journal.state = .reserved
    return true
}
enum CopyFailure: Error { case failed }
func ayuPrepareArchiveSnapshot(media: Media, mediaBox: MediaBox, archiveFileId: Int64, reservationToken: UUID, maximumBytes: Int64) throws -> AyuArchiveSnapshotCandidate {
    Events.values.append("copy")
    if Controls.failCopy { throw CopyFailure.failed }
    return AyuArchiveSnapshotCandidate(sourceResourceId: ayuViewOnceSourceResourceId(media)!, sourceMedia: media,
        archiveFileId: archiveFileId, reservationToken: reservationToken, byteCount: maximumBytes,
        sha256: Data(repeating: 7, count: 32), archivedMedia: media)
}
func ayuCommitPreparedViewOnceCopy(transaction: Transaction, mediaBox: MediaBox, messageId: MessageId, token: UUID, verified: AyuVerifiedArchiveSnapshot) -> MessageId? {
    Events.values.append("commit")
    guard !Controls.failCommit, transaction.journal.state == .reserved else { return nil }
    let archived = MessageId(peerId: messageId.peerId, namespace: Namespaces.Message.Archived, id: 8)
    let candidate = verified.candidate
    transaction.reference = AyuBurnSnapshotReference(originalId: messageId, archivedId: archived, fileId: candidate.archiveFileId,
        token: token, byteCount: candidate.byteCount, sha256: candidate.sha256, sourceResourceId: candidate.sourceResourceId.stringRepresentation)
    transaction.journal.state = .committed
    return archived
}
func ayuPendingBurnReference(transaction: Transaction, originalId: MessageId, state: AyuArchiveJournal.State) -> AyuBurnSnapshotReference? {
    Events.values.append("lookup")
    return transaction.journal.state == state ? transaction.reference : nil
}
'''

tests = r'''
let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: folder) }
let source = folder.appendingPathComponent("source")
try Data([1, 2, 3]).write(to: source)
let id = MessageId(peerId: PeerId(namespace: Namespaces.Peer.CloudUser), namespace: Namespaces.Message.Cloud, id: 7)
func fixture() -> (Account, Transaction) {
    let tx = Transaction(id, Message([TelegramMediaFile(TelegramMediaResource("source-a"))]))
    return (Account(Postbox(MediaBox(folder.path, sourcePath: source.path), tx)), tx)
}
func resetControls() {
    Events.values = []
    Controls.failCopy = false
    Controls.failHash = false
    Controls.failCommit = false
    Controls.failUnlink = false
}
resetControls()
let (account, tx) = fixture()
let prepared = ayuPrepareViewOnceSnapshot(account: account, messageId: id).value
precondition(prepared != nil && prepared?.sourceResourceId == "source-a" && tx.journal.state == .committed)
precondition(activeTestTokens.isEmpty)
precondition(Events.values == ["lookup", "reserve", "copy", "verify", "commit", "lookup"])
Events.values = []
precondition(ayuPrepareViewOnceSnapshot(account: account, messageId: id).value == prepared)
precondition(Events.values == ["lookup", "verify"])
tx.message.media = [TelegramMediaFile(TelegramMediaResource("source-b"))]
Events.values = []
precondition(ayuPrepareViewOnceSnapshot(account: account, messageId: id).value == nil)
precondition(Events.values == ["lookup"] && tx.journal.state == .committed)

resetControls()
Controls.failCopy = true
let (copyAccount, copyTx) = fixture()
precondition(ayuPrepareViewOnceSnapshot(account: copyAccount, messageId: id).value == nil)
precondition(Events.values == ["lookup", "reserve", "copy", "abandon", "unlink", "confirm-cleanup"])
precondition(copyTx.journal.state == nil)

resetControls()
Controls.failHash = true
let (hashAccount, hashTx) = fixture()
precondition(ayuPrepareViewOnceSnapshot(account: hashAccount, messageId: id).value == nil)
precondition(Events.values == ["lookup", "reserve", "copy", "verify", "abandon", "unlink", "confirm-cleanup"])
precondition(hashTx.journal.state == nil)

resetControls()
Controls.failCommit = true
let (commitAccount, commitTx) = fixture()
precondition(ayuPrepareViewOnceSnapshot(account: commitAccount, messageId: id).value == nil)
precondition(Events.values == ["lookup", "reserve", "copy", "verify", "commit", "abandon", "unlink", "confirm-cleanup"])
precondition(commitTx.journal.state == nil)

resetControls()
Controls.failCopy = true
Controls.failUnlink = true
let (unlinkAccount, unlinkTx) = fixture()
precondition(ayuPrepareViewOnceSnapshot(account: unlinkAccount, messageId: id).value == nil)
precondition(Events.values == ["lookup", "reserve", "copy", "abandon", "unlink"])
precondition(unlinkTx.journal.state == .deleting)
print("Offline snapshot coordinator ordering passed with synchronous doubles")
'''

with tempfile.TemporaryDirectory(prefix='ayu-snapshot-flow-') as directory:
    directory = Path(directory)
    main = directory / 'main.swift'
    main.write_text(stubs + '\n\n' + production + '\n\n' + tests, encoding='utf-8')
    binary = directory / 'snapshot-flow-tests'
    subprocess.run(['swiftc', '-warnings-as-errors', str(main), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
