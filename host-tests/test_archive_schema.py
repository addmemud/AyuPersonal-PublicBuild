"""Compile the real archive resource and FlatBuffers branches on a macOS host.

Run from the GitHub workspace after generating TelegramMediaResource_generated.swift:
    python3 feature-archive-schema-tests.py "$RUNNER_TEMP/ayu-flatbuffers"

This isolates and type-checks production serialization branches against pinned
generated code; Postbox itself is represented by a small in-memory test double.
It is not a substitute for building TelegramCore or running its XCTest target.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path("source")
generated = Path(sys.argv[1]) / "TelegramMediaResource_generated.swift"
flatbuffers = sorted((root / "submodules/TelegramCore/FlatBuffers/Sources").rglob("*.swift"))
resource = root / "submodules/TelegramCore/Sources/AyuPrivacy/AyuArchiveMediaResource.swift"
serialization = (root / "submodules/TelegramCore/Sources/SyncCore/SyncCore_CloudFileMediaResource.swift").read_text(encoding="utf-8")
flat_helpers = (root / "submodules/TelegramCore/Sources/Utils/FlatBuffersUtils.swift").read_text(encoding="utf-8").split("public extension PeerId", 1)[0]
if "public func FlatBuffers_getRoot" not in flat_helpers or "public enum FlatBuffersError" not in flat_helpers:
    raise ValueError("Missing production FlatBuffers helper declarations")


def between(text, start, end, occurrence=0):
    parts = text.split(start)
    if len(parts) <= occurrence + 1:
        raise ValueError("Missing production branch: " + start)
    return start + parts[occurrence + 1].split(end, 1)[0]


parse_case = between(serialization, "    case .telegrammediaresourceAyuarchivemediaresource:", "    case .none_:")
serialize_case = between(serialization, "    case let resource as AyuArchiveMediaResource:", "    default:")
id_case = between(serialization, "        case .telegrammediaresourceAyuarchivemediaresource:", "        case .none_:")

postbox_stub = r'''
import Foundation
public struct MediaResourceId: Equatable, Hashable {
    public let stringRepresentation: String
    public init(_ value: String) { stringRepresentation = value }
}
public protocol MediaResource: AnyObject {
    var id: MediaResourceId { get }
    var size: Int64? { get }
    func isEqual(to: MediaResource) -> Bool
}
public protocol PostboxCoding { init(decoder: PostboxDecoder); func encode(_ encoder: PostboxEncoder) }
public final class PostboxEncoder {
    public var fields: [String: Any] = [:]
    public init() {}
    public func encodeInt64(_ value: Int64, forKey key: String) { fields[key] = value }
    public func encodeData(_ value: Data, forKey key: String) { fields[key] = value }
}
public final class PostboxDecoder {
    let fields: [String: Any]
    public init(_ fields: [String: Any]) { self.fields = fields }
    public func decodeInt64ForKey(_ key: String, orElse fallback: Int64) -> Int64 { fields[key] as? Int64 ?? fallback }
    public func decodeDataForKey(_ key: String) -> Data? { fields[key] as? Data }
}
'''

harness = r'''
import Foundation
import Postbox
import FlatBuffers
import FlatSerialization

''' + flat_helpers + r'''
public protocol TelegramMediaResource: MediaResource, PostboxCoding {}

func archiveParse(flatBuffersObject: TelegramCore_TelegramMediaResource) throws -> TelegramMediaResource {
    switch flatBuffersObject.valueType {
''' + parse_case + r'''
    default: throw FlatBuffersError.missingRequiredField()
    }
}
func archiveSerialize(resource: TelegramMediaResource, flatBuffersBuilder builder: inout FlatBufferBuilder) -> Offset? {
    switch resource {
''' + serialize_case + r'''
    default: return nil
    }
}
extension TelegramCore_TelegramMediaResource {
    var testedArchiveId: MediaResourceId {
        switch self.valueType {
''' + id_case + r'''
        default: return MediaResourceId("")
        }
    }
}

let resource = AyuArchiveMediaResource(fileId: -42, size: 8193, sha256: Data(repeating: 0xa5, count: 32))
let encoder = PostboxEncoder()
resource.encode(encoder)
let decoded = AyuArchiveMediaResource(decoder: PostboxDecoder(encoder.fields))
precondition(resource.isEqual(to: decoded) && decoded.id == resource.id)
var builder = FlatBufferBuilder(initialSize: 128)
guard let offset = archiveSerialize(resource: resource, flatBuffersBuilder: &builder) else { fatalError("serialization failed") }
builder.finish(offset: offset)
var buffer = ByteBuffer(data: builder.data)
let object: TelegramCore_TelegramMediaResource = FlatBuffers_getRoot(byteBuffer: &buffer)
let parsed = try archiveParse(flatBuffersObject: object)
precondition(resource.isEqual(to: parsed) && parsed.id == resource.id && object.testedArchiveId == resource.id)
let badSize = AyuArchiveMediaResource(fileId: 1, size: 0, sha256: Data(repeating: 1, count: 32))
var badBuilder = FlatBufferBuilder(initialSize: 128)
precondition(archiveSerialize(resource: badSize, flatBuffersBuilder: &badBuilder) == nil)
let badHash = AyuArchiveMediaResource(fileId: 1, size: 1, sha256: Data(repeating: 1, count: 31))
precondition(archiveSerialize(resource: badHash, flatBuffersBuilder: &badBuilder) == nil)
print("Archive resource real Swift serialization branches round-tripped")
'''


def run(*command, **kwargs):
    subprocess.run([str(part) for part in command], check=True, **kwargs)


if not generated.is_file() or not flatbuffers:
    raise SystemExit("Missing generated model or bundled FlatBuffers Swift sources")
with tempfile.TemporaryDirectory(prefix="ayu-schema-") as tmp:
    directory = Path(tmp)
    postbox = directory / "Postbox.swift"
    postbox.write_text(postbox_stub, encoding="utf-8")
    main = directory / "main.swift"
    main.write_text(harness, encoding="utf-8")
    common = ["-I", directory, "-L", directory, "-Xlinker", "-rpath", "-Xlinker", directory]
    run("swiftc", "-suppress-warnings", "-emit-library", "-emit-module", "-emit-module-path", directory / "FlatBuffers.swiftmodule", "-module-name", "FlatBuffers", "-o", directory / "libFlatBuffers.dylib", *flatbuffers)
    run("swiftc", "-suppress-warnings", "-emit-library", "-emit-module", "-emit-module-path", directory / "FlatSerialization.swiftmodule", "-module-name", "FlatSerialization", "-o", directory / "libFlatSerialization.dylib", *common, "-lFlatBuffers", generated)
    run("swiftc", "-emit-library", "-emit-module", "-emit-module-path", directory / "Postbox.swiftmodule", "-module-name", "Postbox", "-o", directory / "libPostbox.dylib", postbox)
    executable = directory / "archive-schema-tests"
    run("swiftc", "-warnings-as-errors", *common, "-lPostbox", "-lFlatBuffers", "-lFlatSerialization", resource, main, "-o", executable)
    run(executable, env=os.environ.copy())
