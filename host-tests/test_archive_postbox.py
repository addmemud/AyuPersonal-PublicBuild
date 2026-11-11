"""Exercise archive journal storage through the real Postbox Codable codec.

The transaction and hash modules are lightweight test doubles; no real
database, application process, or filesystem recovery is exercised here.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path("source")
postbox = root / "submodules/Postbox/Sources"
privacy = root / "submodules/TelegramCore/Sources/AyuPrivacy"
codec = [postbox / "Coding.swift", postbox / "PreferencesEntry.swift", postbox / "PostboxLogging.swift"]
codec += sorted((postbox / "Utils/Encoder").glob("*.swift"))
codec += sorted((postbox / "Utils/Decoder").glob("*.swift"))
settings_path = root / "submodules/TelegramCore/Sources/Settings/DeletedMessageArchiveSettings.swift"
settings_source = settings_path.read_text(encoding="utf-8")
settings_start = settings_source.index("public struct DeletedMessageArchiveSettings")
settings_open = settings_source.index("{", settings_start)
depth = 0
for position in range(settings_open, len(settings_source)):
    if settings_source[position] == "{":
        depth += 1
    elif settings_source[position] == "}":
        depth -= 1
        if depth == 0:
            settings_declaration = settings_source[settings_start:position + 1]
            break
else:
    raise ValueError("Archive settings declaration is incomplete")

hash_stub = r'''
public func murMurHashString32(_ value: String) -> Int32 {
    var hash: UInt32 = 2166136261
    for byte in value.utf8 { hash = (hash ^ UInt32(byte)) &* 16777619 }
    return Int32(bitPattern: hash)
}
'''
postbox_stub = r'''
import Foundation
public struct MediaResourceId: Hashable {}
public struct ValueBoxKey: Hashable { public init() {} }
public struct MessageId: Hashable, Codable, PostboxCoding {
    public let namespace: Int32
    public let id: Int32
    public init(namespace: Int32, id: Int32) { self.namespace = namespace; self.id = id }
    public init(decoder: PostboxDecoder) {
        namespace = decoder.decodeInt32ForKey("n", orElse: 0)
        id = decoder.decodeInt32ForKey("i", orElse: 0)
    }
    public func encode(_ encoder: PostboxEncoder) {
        encoder.encodeInt32(namespace, forKey: "n")
        encoder.encodeInt32(id, forKey: "i")
    }
    public static func encodeArrayToData(_ ids: [MessageId]) -> Data { try! JSONEncoder().encode(ids) }
    public static func decodeArrayFromData(_ data: Data) -> [MessageId] { (try? JSONDecoder().decode([MessageId].self, from: data)) ?? [] }
}
public protocol MessageAttribute: AnyObject, PostboxCoding { var associatedMessageIds: [MessageId] { get } }
public extension MessageAttribute { var associatedMessageIds: [MessageId] { [] } }
public struct Message {
    public let id: MessageId
    public let attributes: [MessageAttribute]
    public init(id: MessageId, attributes: [MessageAttribute]) { self.id = id; self.attributes = attributes }
}
// MemoryBuffer's ordering depends on a comparator normally defined alongside
// ValueBoxKey; this test does not exercise that ordering.
func mdb_cmp_memn(_ a: UnsafeMutableRawPointer, _ aLength: Int, _ b: UnsafeMutableRawPointer, _ bLength: Int) -> Int {
    let difference = Int(memcmp(a, b, min(aLength, bLength)))
    return difference == 0 ? (aLength == bLength ? 0 : (aLength < bLength ? -1 : 1)) : difference
}
public final class Transaction {
    private var values: [ValueBoxKey: PreferencesEntry] = [:]
    public init() {}
    public func getPreferencesEntry(key: ValueBoxKey) -> PreferencesEntry? { values[key] }
    public func setPreferencesEntry(key: ValueBoxKey, value: PreferencesEntry?) { values[key] = value }
    public func updatePreferencesEntry(key: ValueBoxKey, _ f: (PreferencesEntry?) -> PreferencesEntry?) {
        values[key] = f(values[key])
    }
}
'''
main = r'''
import Foundation
import Postbox

struct PreferencesKeys { static let ayuArchiveJournal = ValueBoxKey() }
enum Namespaces { enum Message { static let Archived: Int32 = 3 } }
let tx = Transaction()
let token = UUID(uuidString: "00000000-0000-0000-0000-000000000001")!
let messageA = AyuArchiveJournal.MessageIdentity(peerId: 10, namespace: 0, id: 1)
let messageB = AyuArchiveJournal.MessageIdentity(peerId: 10, namespace: 0, id: 2)
let hash = Data(repeating: 0xa5, count: 32)
precondition(ayuArchiveJournal(transaction: tx)?.accountedBytes == 0)
precondition(updateAyuArchiveJournal(transaction: tx) {
    $0.reserve(fileId: 7, token: token, message: messageA, bytes: 80, createdAt: 1, limitBytes: 100)
})
precondition(ayuArchiveJournal(transaction: tx)?.accountedBytes == 80)
precondition(updateAyuArchiveJournal(transaction: tx) {
    $0.commit(fileId: 7, token: token, actualBytes: 80, sha256: hash, sourceResourceId: "source-7")
})
let stored = ayuArchiveJournal(transaction: tx)!
precondition(stored.entries.count == 1 && stored.entries[0].sha256 == hash && stored.entries[0].sourceResourceId == "source-7")
precondition(!updateAyuArchiveJournal(transaction: tx) {
    $0.reserve(fileId: 8, token: UUID(), message: messageB, bytes: 21, createdAt: 2, limitBytes: 100)
})
precondition(ayuArchiveJournal(transaction: tx) == stored)
precondition(updateAyuArchiveJournal(transaction: tx) { $0.beginBurn(fileId: 7, token: token) })
precondition(ayuArchiveJournal(transaction: tx)?.entries.first?.state == .burnInFlight)
precondition(!updateAyuArchiveJournal(transaction: tx) { $0.markDeleting(fileId: 7, token: token) })
precondition(updateAyuArchiveJournal(transaction: tx) { $0.confirmBurn(fileId: 7, token: token) })
precondition(ayuArchiveJournal(transaction: tx)?.entries.first?.state == .burned)
precondition(updateAyuArchiveJournal(transaction: tx) { $0.markDeleting(fileId: 7, token: token) })
precondition(updateAyuArchiveJournal(transaction: tx) { $0.confirmDeleted(fileId: 7, token: token) })
precondition(ayuArchiveJournal(transaction: tx)?.retiredIds.contains(7) == true)

// Recovery writes its changes with setPreferencesEntry. Active copy workers
// must retain their reservation while interrupted ones become deletable.
let recoveryTx = Transaction()
let activeToken = UUID(uuidString: "00000000-0000-0000-0000-000000000011")!
let inactiveToken = UUID(uuidString: "00000000-0000-0000-0000-000000000012")!
precondition(updateAyuArchiveJournal(transaction: recoveryTx) {
    $0.reserve(fileId: 11, token: activeToken, message: messageA, bytes: 20, createdAt: 1, limitBytes: 100)
})
precondition(updateAyuArchiveJournal(transaction: recoveryTx) {
    $0.reserve(fileId: 12, token: inactiveToken, message: messageB, bytes: 30, createdAt: 2, limitBytes: 100)
})
let recovered = ayuMarkInactiveArchiveReservations(transaction: recoveryTx, activeTokens: Set([activeToken]))!
precondition(recovered == [AyuArchiveJournal.Deletion(fileId: 12, token: inactiveToken)])
let recoveryJournal = ayuArchiveJournal(transaction: recoveryTx)!
precondition(recoveryJournal.entries.first(where: { $0.fileId == 11 })?.state == .reserved)
precondition(recoveryJournal.entries.first(where: { $0.fileId == 12 })?.state == .deleting)
precondition(recoveryJournal.accountedBytes == 50)

var limits = DeletedMessageArchiveSettings.defaultSettings
limits.mediaLimitGigabytes = DeletedMessageArchiveSettings.limit300Megabytes
limits.disappearingMediaLimitGigabytes = DeletedMessageArchiveSettings.unlimitedMediaLimit
let encodedLimits = PreferencesEntry(limits)!
let restoredLimits = encodedLimits.get(DeletedMessageArchiveSettings.self)!
precondition(restoredLimits == limits)
precondition(restoredLimits.mediaLimitBytes == 300 * 1024 * 1024)
precondition(restoredLimits.disappearingMediaLimitBytes == Int64.max)

// A valid preference containing the wrong type is not mistaken for a new
// journal or overwritten by a later reservation attempt.
struct UnexpectedPreference: Codable { let reason: String }
let invalid = PreferencesEntry(UnexpectedPreference(reason: "wrong journal type"))!
tx.updatePreferencesEntry(key: PreferencesKeys.ayuArchiveJournal) { _ in invalid }
precondition(ayuArchiveJournal(transaction: tx) == nil)
precondition(!updateAyuArchiveJournal(transaction: tx) {
    $0.reserve(fileId: 9, token: token, message: messageA, bytes: 1, createdAt: 3, limitBytes: 100)
})
precondition(tx.getPreferencesEntry(key: PreferencesKeys.ayuArchiveJournal) == invalid)

// Real ArchivedMessageAttribute coding, with stub message IDs and a real
// Postbox codec. An old entry without the new key must stay an ordinary item.
let original = MessageId(namespace: 1, id: 7)
let archived = MessageId(namespace: Namespaces.Message.Archived, id: 8)
let burnAttribute = ArchivedMessageAttribute(originalMessageId: original, versionIds: [], deletedAt: 5,
                                            sessionId: 6, wasDisappearing: true, wasBurned: true)
let encoder = PostboxEncoder()
burnAttribute.encode(encoder)
let decodedBurn = ArchivedMessageAttribute(decoder: PostboxDecoder(buffer: MemoryBuffer(data: encoder.makeData())))
precondition(decodedBurn.originalMessageId == original && decodedBurn.wasBurned && !decodedBurn.pendingBurn)
precondition(Message(id: archived, attributes: [decodedBurn]).isArchivedBurnedMessage)
let legacyEncoder = PostboxEncoder()
legacyEncoder.encodeObject(original, forKey: "o")
let legacyAttribute = ArchivedMessageAttribute(decoder: PostboxDecoder(buffer: MemoryBuffer(data: legacyEncoder.makeData())))
precondition(!legacyAttribute.wasBurned && !Message(id: archived, attributes: [legacyAttribute]).isArchivedBurnedMessage)
precondition(!Message(id: original, attributes: [decodedBurn]).isArchivedBurnedMessage)
print("Archive journal survived real Postbox coding and rejected malformed data")
'''


def run(*command):
    subprocess.run([str(part) for part in command], check=True)


with tempfile.TemporaryDirectory(prefix="ayu-postbox-") as temp:
    directory = Path(temp)
    (directory / "MurMurHash32.swift").write_text(hash_stub, encoding="utf-8")
    (directory / "PostboxStub.swift").write_text(postbox_stub, encoding="utf-8")
    (directory / "ArchiveSettings.swift").write_text("import Foundation\n" + settings_declaration + "\n", encoding="utf-8")
    (directory / "main.swift").write_text(main, encoding="utf-8")
    common = ["-I", directory, "-L", directory, "-Xlinker", "-rpath", "-Xlinker", directory]
    run("swiftc", "-emit-library", "-emit-module", "-emit-module-path", directory / "MurMurHash32.swiftmodule",
        "-module-name", "MurMurHash32", "-o", directory / "libMurMurHash32.dylib", directory / "MurMurHash32.swift")
    run("swiftc", "-suppress-warnings", "-emit-library", "-emit-module", "-emit-module-path", directory / "Postbox.swiftmodule",
        "-module-name", "Postbox", "-o", directory / "libPostbox.dylib", *common, "-lMurMurHash32", *codec,
        directory / "PostboxStub.swift")
    executable = directory / "postbox-tests"
    run("swiftc", "-warnings-as-errors", *common, "-lPostbox", "-lMurMurHash32", privacy / "AyuArchiveJournal.swift",
        privacy / "AyuArchiveJournalStorage.swift", root / "submodules/TelegramCore/Sources/SyncCore/SyncCore_ArchivedMessageAttribute.swift",
        directory / "ArchiveSettings.swift", directory / "main.swift", "-o", executable)
    run(executable)
