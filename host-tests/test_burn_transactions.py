"""Exercise the production Burn transaction guards with in-memory doubles.

This does not send Telegram requests or exercise real Postbox, filesystem,
iOS playback, or UI. The file verification and final message update are stubs.
Run from the preview workflow checkout root on macOS.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path('source')


def declaration(path, start):
    source = (root / path).read_text(encoding='utf-8')
    position = source.index(start)
    opening = source.index('{', position)
    depth = 0
    for i in range(opening, len(source)):
        if source[i] == '{':
            depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0:
                return source[position:i + 1]
    raise ValueError(f'Unclosed declaration: {start}')


privacy = 'submodules/TelegramCore/Sources/AyuPrivacy/'
archive = 'submodules/TelegramCore/Sources/TelegramEngine/Messages/DeletedMessageArchive.swift'
logic = [
    declaration(privacy + 'AyuArchiveJournalStorage.swift', 'func ayuArchiveJournal('),
    declaration(privacy + 'AyuArchiveJournalStorage.swift', 'func ayuCanDiscardArchiveCopies('),
    declaration(privacy + 'AyuArchiveJournalStorage.swift', 'func ayuMarkInactiveArchiveReservations('),
    declaration(privacy + 'AyuArchiveSnapshotPreparation.swift', 'struct AyuBurnSnapshotReference:'),
    declaration(privacy + 'AyuArchiveSnapshotPreparation.swift', 'struct AyuVerifiedBurnSnapshot'),
    declaration(archive, 'func ayuPendingBurnReference('),
    declaration(archive, 'func ayuBeginPreparedViewOnceBurn('),
    declaration(archive, 'func ayuConfirmPreparedViewOnceBurn('),
    declaration(archive, 'func ayuReconcilePendingBurnFromRemoteUpdate('),
    declaration(archive, 'func ayuMarkExplicitArchiveDeletions('),
]

# Guard the known archive cleanup entry points. This checks their wiring,
# while the Swift test below exercises the real journal decision logic.
for signature in (
    'private func prepareDeletedMessageArchive(',
    'private func updateDeletedMessageArchiveMediaRetention(',
    'func archiveMessagesBeforeCloudDeletion(',
    'func _internal_deleteArchivedMessage(',
    'func removeDeletedMessageArchiveForLocalDeletion(',
    'func _internal_clearDeletedMessageArchive(',
    'func _internal_clearDeletedMessageArchiveMedia(',
):
    assert 'ayuCanDiscardArchiveCopies(transaction: transaction)' in declaration(archive, signature), signature
delete_path = 'submodules/TelegramCore/Sources/TelegramEngine/Messages/DeleteMessages.swift'
for signature in ('public func _internal_deleteMessages(', 'func _internal_deleteAllMessagesWithAuthor(',
                  'func _internal_deleteAllMessagesWithForwardAuthor('):
    assert 'ayuCanDiscardArchiveCopies(transaction: transaction)' in declaration(delete_path, signature), signature

stubs = r'''
import Foundation

enum Namespaces {
    enum Peer { static let CloudUser: Int32 = 1 }
    enum Message { static let Cloud: Int32 = 2; static let Archived: Int32 = 3 }
    enum Media { static let AyuArchivedImage: Int32 = 16; static let AyuArchivedFile: Int32 = 17 }
}
struct PeerId: Hashable, Codable {
    let namespace: Int32
    let value: Int64
    func toInt64() -> Int64 { value }
}
struct MessageId: Hashable, Codable { let peerId: PeerId; let namespace: Int32; let id: Int32 }
struct MediaId: Equatable { let namespace: Int32; let id: Int64 }
struct MessageFlags: OptionSet {
    let rawValue: Int
    static let Incoming = MessageFlags(rawValue: 1)
}
struct ConsumableContentMessageAttribute { let consumed: Bool }
struct ArchivedMessageAttribute {
    let originalMessageId: MessageId
    let wasDisappearing: Bool
    var pendingBurn: Bool
    var wasBurned: Bool = false
}
struct AyuArchiveMediaResource {
    let fileId: Int64
    let size: Int64?
    let sha256: Data
}
struct ImageRepresentation { let resource: Any }
struct TelegramMediaImage { let imageId: MediaId; let representations: [ImageRepresentation] }
struct TelegramMediaFile { let fileId: MediaId; let resource: Any; var previewRepresentations: [ImageRepresentation] = [] }
struct TestResourceId { let stringRepresentation: String }
struct SourceMedia { let resourceId: String }
func ayuViewOnceSourceResourceId(_ media: Any) -> TestResourceId? {
    guard let source = media as? SourceMedia else { return nil }
    return TestResourceId(stringRepresentation: source.resourceId)
}
final class Message {
    let id: MessageId
    var flags: MessageFlags = []
    var media: [Any] = []
    var attributes: [Any] = []
    var archivedMessageAttribute: ArchivedMessageAttribute?
    var copyProtected = false
    var ineligible = false
    var paidContent: Int?
    var minAutoremoveOrClearTimeout: Int32?
    init(_ id: MessageId) { self.id = id }
    func isCopyProtected() -> Bool { copyProtected }
}
struct DeletedMessageArchiveIndexEntry: Codable { let messageId: MessageId }
struct DeletedMessageArchiveIndex: Codable {
    var messageIds: [DeletedMessageArchiveIndexEntry]
    var liveMessageIds: [DeletedMessageArchiveIndexEntry] = []
    static let empty = DeletedMessageArchiveIndex(messageIds: [])
}
struct PreferencesEntry {
    private let data: Data
    init?<T: Encodable>(_ value: T) {
        guard let data = try? JSONEncoder().encode(value) else { return nil }
        self.data = data
    }
    func get<T: Decodable>(_ type: T.Type) -> T? { try? JSONDecoder().decode(type, from: data) }
}
enum PreferencesKeys {
    static let ayuArchiveJournal = "journal"
    static let deletedMessageArchiveIndex = "index"
}
final class Transaction {
    var preferences: [String: PreferencesEntry] = [:]
    var messages: [MessageId: Message] = [:]
    var keepsViewOnceMedia = true
    var failFinalize = false
    func getPreferencesEntry(key: String) -> PreferencesEntry? { preferences[key] }
    func setPreferencesEntry(key: String, value: PreferencesEntry) { preferences[key] = value }
    func getMessage(_ id: MessageId) -> Message? { messages[id] }
}
struct GhostSettings { let keepsViewOnceMedia: Bool }
func ghostModeSettings(transaction: Transaction) -> GhostSettings {
    GhostSettings(keepsViewOnceMedia: transaction.keepsViewOnceMedia)
}
func isArchiveIneligible(_ message: Message) -> Bool { message.ineligible }
let viewOnceTimeout: Int32 = Int32.max
enum AyuArchiveFileStore {
    static var valid = true
    static func matches(mediaBoxBasePath: String, fileId: Int64, byteCount: Int64, sha256: Data) -> Bool { valid }
}
func finalizeBurnSnapshot(transaction: Transaction, originalId: MessageId) -> Bool {
    guard !transaction.failFinalize else { return false }
    for message in transaction.messages.values {
        if let attr = message.archivedMessageAttribute, attr.originalMessageId == originalId && attr.pendingBurn {
            message.archivedMessageAttribute?.pendingBurn = false
            message.archivedMessageAttribute?.wasBurned = true
            return true
        }
    }
    return false
}
'''

tests = r'''
let peer = PeerId(namespace: Namespaces.Peer.CloudUser, value: 42)
let originalId = MessageId(peerId: peer, namespace: Namespaces.Message.Cloud, id: 7)
let archivedId = MessageId(peerId: peer, namespace: Namespaces.Message.Archived, id: 8)
let token = UUID(uuidString: "00000000-0000-0000-0000-000000000001")!
let digest = Data(repeating: 0xa5, count: 32)
let reference = AyuBurnSnapshotReference(originalId: originalId, archivedId: archivedId, fileId: 12,
                                         token: token, byteCount: 80, sha256: digest, sourceResourceId: "source-12")

func fixture() -> Transaction {
    let tx = Transaction()
    let original = Message(originalId)
    original.flags = .Incoming
    original.media = [SourceMedia(resourceId: "source-12")]
    original.minAutoremoveOrClearTimeout = viewOnceTimeout
    tx.messages[originalId] = original
    let archived = Message(archivedId)
    archived.archivedMessageAttribute = ArchivedMessageAttribute(originalMessageId: originalId, wasDisappearing: true, pendingBurn: true)
    let resource = AyuArchiveMediaResource(fileId: 12, size: 80, sha256: digest)
    archived.media = [TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.AyuArchivedImage, id: 12),
                                         representations: [ImageRepresentation(resource: resource)])]
    tx.messages[archivedId] = archived
    let index = DeletedMessageArchiveIndex(messageIds: [DeletedMessageArchiveIndexEntry(messageId: archivedId)])
    tx.preferences[PreferencesKeys.deletedMessageArchiveIndex] = PreferencesEntry(index)!
    var journal = AyuArchiveJournal()
    let identity = AyuArchiveJournal.MessageIdentity(peerId: peer.toInt64(), namespace: originalId.namespace, id: originalId.id)
    precondition(journal.reserve(fileId: 12, token: token, message: identity, bytes: 80, createdAt: 1, limitBytes: 100))
    precondition(journal.commit(fileId: 12, token: token, actualBytes: 80, sha256: digest, sourceResourceId: "source-12"))
    tx.preferences[PreferencesKeys.ayuArchiveJournal] = PreferencesEntry(journal)!
    return tx
}

func ready(_ tx: Transaction) -> Bool {
    guard let verified = AyuVerifiedBurnSnapshot.verify(reference, mediaBoxBasePath: "unused") else { return false }
    return ayuBeginPreparedViewOnceBurn(transaction: tx, verified: verified)
}

let tx = fixture()
precondition(ayuCanDiscardArchiveCopies(transaction: tx))
let damagedJournal = fixture()
damagedJournal.preferences[PreferencesKeys.ayuArchiveJournal] = PreferencesEntry("not a journal")!
precondition(!ayuCanDiscardArchiveCopies(transaction: damagedJournal))
precondition(ayuPendingBurnReference(transaction: tx, originalId: originalId, state: .committed) == reference)
AyuArchiveFileStore.valid = false
precondition(!ready(tx))
AyuArchiveFileStore.valid = true
tx.keepsViewOnceMedia = false
precondition(!ready(tx))
tx.keepsViewOnceMedia = true
tx.messages[originalId]!.flags = []
precondition(!ready(tx))
tx.messages[originalId]!.flags = .Incoming
tx.messages[originalId]!.attributes = [ConsumableContentMessageAttribute(consumed: true)]
precondition(!ready(tx))
tx.messages[originalId]!.attributes = []
tx.messages[originalId]!.copyProtected = true
precondition(!ready(tx))
tx.messages[originalId]!.copyProtected = false
tx.messages[originalId]!.ineligible = true
precondition(!ready(tx))
tx.messages[originalId]!.ineligible = false
tx.messages[originalId]!.paidContent = 1
precondition(!ready(tx))
tx.messages[originalId]!.paidContent = nil
tx.messages[originalId]!.minAutoremoveOrClearTimeout = 10
precondition(!ready(tx))
tx.messages[originalId]!.minAutoremoveOrClearTimeout = viewOnceTimeout
tx.messages[originalId]!.media = [SourceMedia(resourceId: "changed-source")]
precondition(!ready(tx))
tx.messages[originalId]!.media = [SourceMedia(resourceId: "source-12")]

let duplicate = fixture()
let repeated = DeletedMessageArchiveIndex(messageIds: [DeletedMessageArchiveIndexEntry(messageId: archivedId), DeletedMessageArchiveIndexEntry(messageId: archivedId)])
duplicate.preferences[PreferencesKeys.deletedMessageArchiveIndex] = PreferencesEntry(repeated)!
precondition(!ready(duplicate))
let missing = fixture()
missing.messages.removeValue(forKey: archivedId)
precondition(!ready(missing))
let contradictory = fixture()
contradictory.messages[archivedId]!.archivedMessageAttribute?.wasBurned = true
precondition(!ready(contradictory))
let wrongResource = fixture()
let wrongDigest = AyuArchiveMediaResource(fileId: 12, size: 80, sha256: Data(repeating: 0, count: 32))
wrongResource.messages[archivedId]!.media = [TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.AyuArchivedFile, id: 12), resource: wrongDigest)]
precondition(!ready(wrongResource))
let wrongToken = AyuBurnSnapshotReference(originalId: originalId, archivedId: archivedId, fileId: 12,
                                           token: UUID(), byteCount: 80, sha256: digest, sourceResourceId: "source-12")
precondition(!ayuBeginPreparedViewOnceBurn(transaction: tx, verified: AyuVerifiedBurnSnapshot.verify(wrongToken, mediaBoxBasePath: "unused")!))
precondition(ready(tx))
precondition(!ayuCanDiscardArchiveCopies(transaction: tx))
precondition(!ready(tx))
var inFlight = ayuArchiveJournal(transaction: tx)!
precondition(inFlight.entries[0].state == .burnInFlight)
precondition(!inFlight.markDeleting(fileId: 12, token: token))
precondition(!ayuConfirmPreparedViewOnceBurn(transaction: tx, reference: wrongToken))
tx.failFinalize = true
precondition(!ayuConfirmPreparedViewOnceBurn(transaction: tx, reference: reference))
precondition(ayuArchiveJournal(transaction: tx)!.entries[0].state == .burnInFlight)
tx.failFinalize = false
precondition(ayuConfirmPreparedViewOnceBurn(transaction: tx, reference: reference))
precondition(ayuCanDiscardArchiveCopies(transaction: tx))
precondition(ayuArchiveJournal(transaction: tx)!.entries[0].state == .burned)
precondition(tx.messages[archivedId]!.archivedMessageAttribute!.pendingBurn == false)
precondition(ayuConfirmPreparedViewOnceBurn(transaction: tx, reference: reference))
precondition(!ayuConfirmPreparedViewOnceBurn(transaction: tx, reference: wrongToken))

// A server update may precede the HTTP completion or arrive after a restart.
let updateTx = fixture()
precondition(!ayuReconcilePendingBurnFromRemoteUpdate(transaction: updateTx, originalId: originalId))
precondition(ready(updateTx))
precondition(ayuReconcilePendingBurnFromRemoteUpdate(transaction: updateTx, originalId: originalId))
precondition(ayuArchiveJournal(transaction: updateTx)!.entries[0].state == .burned)
precondition(ayuReconcilePendingBurnFromRemoteUpdate(transaction: updateTx, originalId: originalId))
precondition(ayuConfirmPreparedViewOnceBurn(transaction: updateTx, reference: reference))

// Explicit cleanup must record an owned file before the archive entry can be
// removed; unknown Burn outcomes and incorrect resource identities fail closed.
let deleting = fixture()
let marked = ayuMarkExplicitArchiveDeletions(transaction: deleting, selectedIds: [archivedId])!
precondition(marked == [AyuArchiveJournal.Deletion(fileId: 12, token: token)])
precondition(ayuArchiveJournal(transaction: deleting)!.entries[0].state == .deleting)
precondition(!ready(deleting))
let unsafeCleanup = fixture()
precondition(ready(unsafeCleanup))
precondition(ayuMarkExplicitArchiveDeletions(transaction: unsafeCleanup, selectedIds: [archivedId]) == nil)
precondition(ayuArchiveJournal(transaction: unsafeCleanup)!.entries[0].state == .burnInFlight)
let mismatchedCleanup = fixture()
mismatchedCleanup.messages[archivedId]!.media = [TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.AyuArchivedFile, id: 12), resource: wrongDigest)]
precondition(ayuMarkExplicitArchiveDeletions(transaction: mismatchedCleanup, selectedIds: [archivedId]) == nil)
precondition(ayuArchiveJournal(transaction: mismatchedCleanup)!.entries[0].state == .committed)
let orphaned = fixture()
var orphanJournal = ayuArchiveJournal(transaction: orphaned)!
let orphanIdentity = AyuArchiveJournal.MessageIdentity(peerId: peer.toInt64(), namespace: originalId.namespace, id: 99)
precondition(orphanJournal.reserve(fileId: 13, token: UUID(), message: orphanIdentity, bytes: 1, createdAt: 2, limitBytes: 100))
let orphanToken = orphanJournal.entries.first(where: { $0.fileId == 13 })!.token
precondition(orphanJournal.commit(fileId: 13, token: orphanToken, actualBytes: 1, sha256: digest, sourceResourceId: "source-13"))
orphaned.preferences[PreferencesKeys.ayuArchiveJournal] = PreferencesEntry(orphanJournal)!
precondition(ayuMarkExplicitArchiveDeletions(transaction: orphaned, selectedIds: nil, requireAllJournalEntries: true) == nil)
precondition(ayuArchiveJournal(transaction: orphaned) == orphanJournal)
let interrupted = fixture()
var interruptedJournal = ayuArchiveJournal(transaction: interrupted)!
let reservedToken = UUID()
precondition(interruptedJournal.reserve(fileId: 14, token: reservedToken, message: orphanIdentity, bytes: 1, createdAt: 3, limitBytes: 100))
interrupted.preferences[PreferencesKeys.ayuArchiveJournal] = PreferencesEntry(interruptedJournal)!
precondition(ayuMarkInactiveArchiveReservations(transaction: interrupted, activeTokens: [reservedToken]) == [])
precondition(ayuArchiveJournal(transaction: interrupted)!.entries.first(where: { $0.fileId == 14 })!.state == .reserved)
precondition(ayuMarkInactiveArchiveReservations(transaction: interrupted, activeTokens: []) == [AyuArchiveJournal.Deletion(fileId: 14, token: reservedToken)])
precondition(ayuArchiveJournal(transaction: interrupted)!.entries.first(where: { $0.fileId == 14 })!.state == .deleting)
precondition(ayuArchiveJournal(transaction: interrupted)!.entries.first(where: { $0.fileId == 12 })!.state == .committed)
print("Burn transaction guards passed with in-memory doubles")
'''

with tempfile.TemporaryDirectory(prefix='ayu-burn-guards-') as directory:
    directory = Path(directory)
    main = directory / 'main.swift'
    main.write_text(stubs + '\n\n' + '\n\n'.join(logic) + '\n\n' + tests, encoding='utf-8')
    binary = directory / 'burn-guards'
    subprocess.run(['swiftc', '-warnings-as-errors', str(root / privacy / 'AyuArchiveJournal.swift'), str(main), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
