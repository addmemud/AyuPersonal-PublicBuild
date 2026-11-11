from pathlib import Path
import subprocess
import tempfile

root = Path('source')
store = root / 'submodules/TelegramCore/Sources/AyuPrivacy/AyuArchiveFileStore.swift'
fetch = (root / 'submodules/TelegramCore/Sources/State/Fetch.swift').read_text(encoding='utf-8')
copy_class = fetch.split('private final class MediaResourceDataCopyFile', 1)[1].split('\nfunc fetchCloudMediaLocation(', 1)[0]
test = r'''
import Foundation

protocol MediaResourceDataFetchCopyLocalItem {
    func copyTo(url: URL) -> Bool
}
'''+ 'private final class MediaResourceDataCopyFile' + copy_class + r'''

let fm = FileManager.default
let root = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
defer { try? fm.removeItem(at: root) }
let media = root.appendingPathComponent("postbox/media", isDirectory: true)
try fm.createDirectory(at: media, withIntermediateDirectories: true)
let source = media.appendingPathComponent("original")
let input = Data((0 ..< 2_100_000).map { UInt8($0 % 251) })
try input.write(to: source)
let token = UUID(uuidString: "00000000-0000-0000-0000-000000000001")!
let otherToken = UUID(uuidString: "00000000-0000-0000-0000-000000000002")!
precondition(AyuArchiveFileStore.temporaryURL(mediaBoxBasePath: media.path, fileId: 12, token: token) != AyuArchiveFileStore.temporaryURL(mediaBoxBasePath: media.path, fileId: 12, token: otherToken))
precondition(AyuArchiveFileStore.temporaryURL(mediaBoxBasePath: media.path, fileId: 12, token: token) != AyuArchiveFileStore.temporaryURL(mediaBoxBasePath: media.path, fileId: 13, token: token))
let count = try AyuArchiveFileStore.copy(mediaBoxBasePath: media.path, fileId: 12, token: token, sourcePath: source.path, expectedSize: Int64(input.count), maximumBytes: Int64(input.count))
precondition(count.byteCount == Int64(input.count))
let saved = AyuArchiveFileStore.fileURL(mediaBoxBasePath: media.path, fileId: 12)
try fm.removeItem(at: media)
let savedAfterRemoval = try Data(contentsOf: saved)
precondition(savedAfterRemoval == input)
let savedHash = try AyuArchiveFileStore.sha256(path: saved.path)
precondition(savedHash == count.sha256)
precondition(AyuArchiveFileStore.matches(mediaBoxBasePath: media.path, fileId: 12, byteCount: count.byteCount, sha256: count.sha256))
precondition(!AyuArchiveFileStore.matches(mediaBoxBasePath: media.path, fileId: 12, byteCount: count.byteCount + 1, sha256: count.sha256))
try fm.createDirectory(at: media, withIntermediateDirectories: true)
let restored = media.appendingPathComponent("restored")
precondition(MediaResourceDataCopyFile(path: saved.path).copyTo(url: restored))
let restoredBytes = try Data(contentsOf: restored)
precondition(restoredBytes == input)
let protectedCopy = media.appendingPathComponent("verified")
precondition(AyuArchiveMediaResourceDataCopyFile(path: saved.path, expectedDigest: count.sha256).copyTo(url: protectedCopy))
var damaged = input
damaged[0] ^= 0xff
try damaged.write(to: saved)
precondition(!AyuArchiveFileStore.matches(mediaBoxBasePath: media.path, fileId: 12, byteCount: count.byteCount, sha256: count.sha256))
let rejected = media.appendingPathComponent("rejected")
precondition(!AyuArchiveMediaResourceDataCopyFile(path: saved.path, expectedDigest: count.sha256).copyTo(url: rejected))
precondition(!fm.fileExists(atPath: rejected.path))
precondition(!AyuArchiveMediaResourceDataCopyFile(path: saved.path, expectedDigest: count.sha256).copyTo(url: protectedCopy))
let protectedAfterFailure = try Data(contentsOf: protectedCopy)
precondition(protectedAfterFailure == input)
try input.write(to: saved)
try Data([1]).write(to: source)
do {
    _ = try AyuArchiveFileStore.copy(mediaBoxBasePath: media.path, fileId: 12, token: token, sourcePath: source.path, expectedSize: 1, maximumBytes: 1)
    fatalError("existing copy was overwritten")
} catch AyuArchiveFileStore.CopyError.destinationExists {}
let savedAfterCollision = try Data(contentsOf: saved)
precondition(savedAfterCollision == input)
let empty = media.appendingPathComponent("empty")
try Data().write(to: empty)
do {
    _ = try AyuArchiveFileStore.copy(mediaBoxBasePath: media.path, fileId: 13, token: token, sourcePath: empty.path, expectedSize: 0, maximumBytes: 1)
    fatalError("empty copy was accepted")
} catch AyuArchiveFileStore.CopyError.emptyFile {}
precondition(!fm.fileExists(atPath: AyuArchiveFileStore.fileURL(mediaBoxBasePath: media.path, fileId: 13).path))
do {
    _ = try AyuArchiveFileStore.copy(mediaBoxBasePath: media.path, fileId: 14, token: token, sourcePath: source.path, expectedSize: 2, maximumBytes: 1)
    fatalError("oversized copy was accepted")
} catch AyuArchiveFileStore.CopyError.sizeLimitExceeded {}
precondition(!fm.fileExists(atPath: AyuArchiveFileStore.fileURL(mediaBoxBasePath: media.path, fileId: 14).path))
do {
    _ = try AyuArchiveFileStore.copy(mediaBoxBasePath: media.path, fileId: 15, token: token, sourcePath: source.path, expectedSize: 2, maximumBytes: 2)
    fatalError("changed source was accepted")
} catch AyuArchiveFileStore.CopyError.sourceChanged {}
precondition(!fm.fileExists(atPath: AyuArchiveFileStore.fileURL(mediaBoxBasePath: media.path, fileId: 15).path))
// A temporary file belonging to an interrupted reservation must never be
// overwritten or removed by a second attempt using the same identity.
let existingTemporary = AyuArchiveFileStore.temporaryURL(mediaBoxBasePath: media.path, fileId: 16, token: token)
try Data([42]).write(to: existingTemporary)
do {
    _ = try AyuArchiveFileStore.copy(mediaBoxBasePath: media.path, fileId: 16, token: token, sourcePath: source.path, expectedSize: 1, maximumBytes: 1)
    fatalError("existing temporary copy was overwritten")
} catch AyuArchiveFileStore.CopyError.temporaryExists {}
let temporaryAfterRetry = try Data(contentsOf: existingTemporary)
precondition(temporaryAfterRetry == Data([42]))
precondition(!fm.fileExists(atPath: AyuArchiveFileStore.fileURL(mediaBoxBasePath: media.path, fileId: 16).path))
let unrelatedTemporary = AyuArchiveFileStore.temporaryURL(mediaBoxBasePath: media.path, fileId: 16, token: otherToken)
try Data([43]).write(to: unrelatedTemporary)
precondition(AyuArchiveFileStore.removeAbandonedReservationFiles(mediaBoxBasePath: media.path, fileId: 16, token: token))
precondition(!fm.fileExists(atPath: existingTemporary.path))
precondition(fm.fileExists(atPath: unrelatedTemporary.path))
precondition(AyuArchiveFileStore.removeAbandonedReservationFiles(mediaBoxBasePath: media.path, fileId: 16, token: token))
try fm.removeItem(at: unrelatedTemporary)
let abandonedFinal = AyuArchiveFileStore.fileURL(mediaBoxBasePath: media.path, fileId: 18)
let abandonedTemporary = AyuArchiveFileStore.temporaryURL(mediaBoxBasePath: media.path, fileId: 18, token: token)
let otherFinal = AyuArchiveFileStore.fileURL(mediaBoxBasePath: media.path, fileId: 19)
try Data([1]).write(to: abandonedFinal)
try Data([2]).write(to: abandonedTemporary)
try Data([3]).write(to: otherFinal)
precondition(AyuArchiveFileStore.removeAbandonedReservationFiles(mediaBoxBasePath: media.path, fileId: 18, token: token))
precondition(!fm.fileExists(atPath: abandonedFinal.path) && !fm.fileExists(atPath: abandonedTemporary.path))
precondition(fm.fileExists(atPath: otherFinal.path))
try fm.removeItem(at: otherFinal)
let directoryInsteadOfFile = AyuArchiveFileStore.temporaryURL(mediaBoxBasePath: media.path, fileId: 17, token: token)
try fm.createDirectory(at: directoryInsteadOfFile, withIntermediateDirectories: true)
precondition(!AyuArchiveFileStore.removeAbandonedReservationFiles(mediaBoxBasePath: media.path, fileId: 17, token: token))
try fm.removeItem(at: directoryInsteadOfFile)
let leftovers = try fm.contentsOfDirectory(atPath: saved.deletingLastPathComponent().path)
precondition(!leftovers.contains { $0.hasPrefix(".tmp-") })
print("Archive copy and cache removal checks passed")
'''

with tempfile.TemporaryDirectory(prefix='ayu-archive-store-') as directory:
    directory = Path(directory)
    main = directory / 'main.swift'
    main.write_text(test, encoding='utf-8')
    binary = directory / 'archive-tests'
    subprocess.run(['swiftc', '-warnings-as-errors', str(store), str(main), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
