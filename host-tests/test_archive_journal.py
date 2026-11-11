"""Run the experimental journal's production Swift logic on macOS.

This does not exercise Postbox transactions, filesystem GC, or iOS playback.
Run from the root of the preview workflow checkout.
"""
from pathlib import Path
import subprocess
import tempfile

source = Path("source/submodules/TelegramCore/Sources/AyuPrivacy/AyuArchiveJournal.swift")
archive = Path("source/submodules/TelegramCore/Sources/TelegramEngine/Messages/DeletedMessageArchive.swift").read_text(encoding="utf-8")
retention = "public func deletedMessageArchiveRetentionPlan" + archive.split("public func deletedMessageArchiveRetentionPlan", 1)[1].split("@discardableResult\nprivate func updateDeletedMessageArchiveMediaRetention", 1)[0]
test = r'''
import Foundation
''' + retention + r'''

let first = UUID(uuidString: "00000000-0000-0000-0000-000000000001")!
let second = UUID(uuidString: "00000000-0000-0000-0000-000000000002")!
let wrong = UUID(uuidString: "00000000-0000-0000-0000-000000000003")!
let messageA = AyuArchiveJournal.MessageIdentity(peerId: 10, namespace: 0, id: 1)
let messageB = AyuArchiveJournal.MessageIdentity(peerId: 10, namespace: 0, id: 2)
let messageC = AyuArchiveJournal.MessageIdentity(peerId: 10, namespace: 0, id: 3)
var abandoned = AyuArchiveJournal()
precondition(abandoned.reserve(fileId: 30, token: first, message: messageA, bytes: 10, createdAt: 1, limitBytes: 100))
precondition(!abandoned.confirmAbandonedReservationDeleted(fileId: 30, token: first))
precondition(!abandoned.abandonReservation(fileId: 30, token: wrong))
precondition(abandoned.abandonReservation(fileId: 30, token: first))
precondition(!abandoned.commit(fileId: 30, token: first, actualBytes: 10, sha256: Data(repeating: 1, count: 32)))
precondition(abandoned.recoveryActions(activeTokens: [], reservationsOlderThan: 100) == [.inspectPendingDeletion(fileId: 30, token: first)])
precondition(!abandoned.confirmAbandonedReservationDeleted(fileId: 30, token: wrong))
precondition(abandoned.confirmAbandonedReservationDeleted(fileId: 30, token: first))
precondition(!abandoned.reserve(fileId: 30, token: second, message: messageB, bytes: 1, createdAt: 2, limitBytes: 100))
precondition(ayuArchiveJournalBudget(totalLimit: 100, disappearingLimit: 60, cachedTotal: 30, cachedDisappearing: 10) == 50)
precondition(ayuArchiveJournalBudget(totalLimit: 100, disappearingLimit: 60, cachedTotal: 120, cachedDisappearing: 10) == 0)
precondition(ayuArchiveJournalBudget(totalLimit: 100, disappearingLimit: 60, cachedTotal: 5, cachedDisappearing: 10) == nil)
precondition(ayuArchiveJournalBudget(totalLimit: -1, disappearingLimit: 60, cachedTotal: 0, cachedDisappearing: 0) == nil)
let cacheBudgets = ayuArchiveCacheBudgets(totalLimit: 100, disappearingLimit: 60, journalBytes: 30)!
precondition(cacheBudgets.total == 70 && cacheBudgets.disappearing == 30)
let fullJournal = ayuArchiveCacheBudgets(totalLimit: 100, disappearingLimit: 60, journalBytes: 120)!
precondition(fullJournal.total == 0 && fullJournal.disappearing == 0)
if let _ = ayuArchiveCacheBudgets(totalLimit: 100, disappearingLimit: 60, journalBytes: -1) {
    fatalError("negative journal usage was accepted")
}
let tighterBudgets = ayuArchiveCacheBudgets(totalLimit: 100, disappearingLimit: 60, journalBytes: 50)!
let retentionPlan = deletedMessageArchiveRetentionPlan(
    items: [(id: 1, size: 40, isDisappearing: false), (id: 2, size: 25, isDisappearing: true)],
    limitBytes: tighterBudgets.total,
    disappearingLimitBytes: tighterBudgets.disappearing)
precondition(retentionPlan.retained == [1] && retentionPlan.removed == [2])
var journal = AyuArchiveJournal()
precondition(journal.isValid && journal.accountedBytes == 0)
precondition(journal.reserve(fileId: 7, token: first, message: messageA, bytes: 80, createdAt: 1, limitBytes: 100))
precondition(!journal.reserve(fileId: 8, token: second, message: messageB, bytes: 21, createdAt: 2, limitBytes: 100))
precondition(!journal.reserve(fileId: 7, token: second, message: messageB, bytes: 20, createdAt: 2, limitBytes: 100))
precondition(!journal.reserve(fileId: 8, token: first, message: messageB, bytes: 20, createdAt: 2, limitBytes: 100))
precondition(!journal.reserve(fileId: 8, token: second, message: messageA, bytes: 20, createdAt: 2, limitBytes: 100))
precondition(journal.reserve(fileId: 8, token: second, message: messageB, bytes: 20, createdAt: 2, limitBytes: 100))
let hash = Data(repeating: 0xa5, count: 32)
precondition(!journal.commit(fileId: 7, token: wrong, actualBytes: 80, sha256: hash))
precondition(!journal.commit(fileId: 7, token: first, actualBytes: 79, sha256: hash))
precondition(!journal.commit(fileId: 7, token: first, actualBytes: 80, sha256: Data([1])))
precondition(journal.commit(fileId: 7, token: first, actualBytes: 80, sha256: hash))
precondition(!journal.commit(fileId: 7, token: first, actualBytes: 80, sha256: hash))
precondition(!journal.beginBurn(fileId: 7, token: first)) // old entries lack source identity

// Simulate restart with one committed file and one copy in flight.
let saved = try JSONEncoder().encode(journal)
var recovered = try JSONDecoder().decode(AyuArchiveJournal.self, from: saved)
precondition(recovered == journal && recovered.isValid && recovered.accountedBytes == 100)
precondition(recovered.recoveryActions(activeTokens: [second], reservationsOlderThan: 100).isEmpty)
precondition(recovered.recoveryActions(activeTokens: [], reservationsOlderThan: 1).isEmpty)
precondition(recovered.recoveryActions(activeTokens: [], reservationsOlderThan: 2) == [.inspectAbandonedReservation(fileId: 8, token: second)])
precondition(recovered.markDeleting(fileId: 7, token: first))
precondition(recovered.recoveryActions(activeTokens: [first, second], reservationsOlderThan: 100).isEmpty)
precondition(recovered.recoveryActions(activeTokens: [second], reservationsOlderThan: 100) == [.inspectPendingDeletion(fileId: 7, token: first)])
precondition(recovered.accountedBytes == 100) // quota is held until unlink succeeds
precondition(!recovered.confirmDeleted(fileId: 7, token: wrong))
precondition(!recovered.reserve(fileId: 9, token: wrong, message: messageC, bytes: 1, createdAt: 3, limitBytes: 100))
recovered = try JSONDecoder().decode(AyuArchiveJournal.self, from: JSONEncoder().encode(recovered))
precondition(recovered.confirmDeleted(fileId: 7, token: first))
precondition(recovered.accountedBytes == 20)
precondition(!recovered.reserve(fileId: 7, token: wrong, message: messageC, bytes: 1, createdAt: 3, limitBytes: 100))
precondition(recovered.reserve(fileId: 9, token: wrong, message: messageC, bytes: 80, createdAt: 3, limitBytes: 100))

// A remote burn is never automatic after a crash with an unknown result, and
// its preserved copy cannot be deleted while the result is unresolved.
var burn = AyuArchiveJournal()
precondition(burn.reserve(fileId: 20, token: first, message: messageA, bytes: 10, createdAt: 1, limitBytes: 100))
precondition(!burn.beginBurn(fileId: 20, token: first))
precondition(!burn.commit(fileId: 20, token: first, actualBytes: 10, sha256: hash, sourceResourceId: ""))
precondition(burn.commit(fileId: 20, token: first, actualBytes: 10, sha256: hash, sourceResourceId: "source-20"))
precondition(burn.beginBurn(fileId: 20, token: first))
precondition(!burn.beginBurn(fileId: 20, token: first))
precondition(!burn.markDeleting(fileId: 20, token: first))
precondition(burn.recoveryActions(activeTokens: [first], reservationsOlderThan: 100).isEmpty)
burn = try JSONDecoder().decode(AyuArchiveJournal.self, from: JSONEncoder().encode(burn))
precondition(burn.entries[0].sourceResourceId == "source-20")
precondition(burn.recoveryActions(activeTokens: [], reservationsOlderThan: 100) == [.inspectUnconfirmedBurn(fileId: 20, token: first)])
precondition(!burn.confirmBurn(fileId: 20, token: wrong))
precondition(burn.confirmBurn(fileId: 20, token: first))
precondition(burn.recoveryActions(activeTokens: [], reservationsOlderThan: 100).isEmpty)
precondition(burn.markDeleting(fileId: 20, token: first))

// Explicit deletion is atomic, holds quota until unlink confirmation and
// refuses both active copy workers and ambiguous remote burns.
var explicit = AyuArchiveJournal()
precondition(explicit.reserve(fileId: 40, token: first, message: messageA, bytes: 10, createdAt: 1, limitBytes: 100))
precondition(explicit.commit(fileId: 40, token: first, actualBytes: 10, sha256: hash, sourceResourceId: "source-40"))
precondition(explicit.reserve(fileId: 41, token: second, message: messageB, bytes: 20, createdAt: 2, limitBytes: 100))
let beforeCopyFinished = explicit
precondition(explicit.markExplicitDeletions(fileIds: [40]) == nil)
precondition(explicit == beforeCopyFinished)
precondition(explicit.commit(fileId: 41, token: second, actualBytes: 20, sha256: hash, sourceResourceId: "source-41"))
let indexed = [
    AyuArchiveIndexedFile(key: 1, original: messageA, fileId: 40, byteCount: 10, sha256: hash),
    AyuArchiveIndexedFile(key: 2, original: messageA, fileId: 40, byteCount: 10, sha256: hash),
    AyuArchiveIndexedFile(key: 3, original: messageB, fileId: 41, byteCount: 20, sha256: hash)
]
precondition(ayuArchiveFileIdsForExplicitDeletion(journal: explicit, indexed: indexed, selected: [1]) == [])
precondition(ayuArchiveFileIdsForExplicitDeletion(journal: explicit, indexed: indexed, selected: [1, 2]) == [40])
precondition(ayuArchiveFileIdsForExplicitDeletion(journal: explicit, indexed: indexed, selected: [1, 2, 3]) == [40, 41])
let mismatched = [AyuArchiveIndexedFile(key: 1, original: messageB, fileId: 40, byteCount: 10, sha256: hash)]
precondition(ayuArchiveFileIdsForExplicitDeletion(journal: explicit, indexed: mismatched, selected: [1]) == nil)
var alreadyCleared = explicit
precondition(alreadyCleared.markDeleting(fileId: 40, token: first))
precondition(alreadyCleared.confirmDeleted(fileId: 40, token: first))
precondition(ayuArchiveFileIdsForExplicitDeletion(journal: alreadyCleared, indexed: indexed, selected: [1, 2]) == [])
let unknown = [AyuArchiveIndexedFile(key: 1, original: messageA, fileId: 999, byteCount: 10, sha256: hash)]
precondition(ayuArchiveFileIdsForExplicitDeletion(journal: alreadyCleared, indexed: unknown, selected: [1]) == nil)
let beforeInvalidSelection = explicit
precondition(explicit.markExplicitDeletions(fileIds: [40, 99]) == nil)
precondition(explicit == beforeInvalidSelection)
precondition(explicit.beginBurn(fileId: 41, token: second))
let beforeUnknownBurn = explicit
precondition(explicit.markExplicitDeletions(fileIds: [40]) == nil)
precondition(explicit == beforeUnknownBurn)
precondition(explicit.confirmBurn(fileId: 41, token: second))
let requested = explicit.markExplicitDeletions(fileIds: [40, 41])!
precondition(Set(requested.map(\.fileId)) == [40, 41])
precondition(explicit.accountedBytes == 30)
precondition(explicit.entries.allSatisfy { $0.state == .deleting })
precondition(explicit.confirmDeleted(fileId: 40, token: first))
precondition(explicit.accountedBytes == 20)
precondition(!explicit.reserve(fileId: 40, token: wrong, message: messageC, bytes: 1, createdAt: 3, limitBytes: 100))

// Overflow and malformed persisted state must fail closed.
var overflow = AyuArchiveJournal()
precondition(overflow.reserve(fileId: 10, token: first, message: messageA, bytes: Int64.max, createdAt: 0, limitBytes: Int64.max))
precondition(!overflow.reserve(fileId: 11, token: second, message: messageB, bytes: 1, createdAt: 0, limitBytes: Int64.max))
var object = try JSONSerialization.jsonObject(with: saved) as! [String: Any]
var entries = object["entries"] as! [[String: Any]]
entries.append(entries[0])
object["entries"] = entries
let corrupt = try JSONSerialization.data(withJSONObject: object)
var invalid = try JSONDecoder().decode(AyuArchiveJournal.self, from: corrupt)
precondition(!invalid.isValid)
precondition(invalid.recoveryActions(activeTokens: [], reservationsOlderThan: 100).isEmpty)
precondition(!invalid.reserve(fileId: 99, token: wrong, message: messageC, bytes: 1, createdAt: 0, limitBytes: 1000))
print("Archive reservation, restart and deletion checks passed")
'''

with tempfile.TemporaryDirectory(prefix="ayu-journal-") as directory:
    directory = Path(directory)
    main = directory / "main.swift"
    main.write_text(test, encoding="utf-8")
    executable = directory / "journal-tests"
    subprocess.run(["swiftc", "-warnings-as-errors", str(source), str(main), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
