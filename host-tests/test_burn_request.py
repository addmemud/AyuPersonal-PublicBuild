"""Exercise the real Burn request ordering with synchronous test doubles.

This test never connects to Telegram. It does not prove delivery, other-device
behavior, real Postbox atomicity, or iOS playback.
"""
from pathlib import Path
import subprocess
import tempfile

source = Path('source/submodules/TelegramCore/Sources/AyuPrivacy/AyuViewOnceBurnRequest.swift').read_text(encoding='utf-8')


def declaration(start):
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


production = '\n\n'.join(declaration(name) for name in (
    'enum AyuBurnAttemptResult:', 'func ayuSendPreparedViewOnceBurn(', 'func ayuAttemptViewOnceBurn('
))
stubs = r'''
import Foundation
enum NoError: Error {}
enum NetworkError: Error { case disconnected }
struct Signal<Value, Failure: Error> {
    let result: Result<Value, Failure>
    static func single(_ value: Value) -> Signal<Value, Failure> { Signal(result: .success(value)) }
    static func fail(_ error: Failure) -> Signal<Value, Failure> { Signal(result: .failure(error)) }
}
infix operator |>: AdditionPrecedence
func |> <T, U>(value: T, transform: (T) -> U) -> U { transform(value) }
final class Queue { static func concurrentDefaultQueue() -> Queue { Queue() } }
func deliverOn<T, E: Error>(_ queue: Queue) -> (Signal<T, E>) -> Signal<T, E> { { $0 } }
func map<T, U, E: Error>(_ f: @escaping (T) -> U) -> (Signal<T, E>) -> Signal<U, E> {
    { signal in Signal<U, E>(result: signal.result.map(f)) }
}
func mapToSignal<T, U, E: Error>(_ f: @escaping (T) -> Signal<U, E>) -> (Signal<T, E>) -> Signal<U, E> {
    { signal in
        switch signal.result {
        case let .success(value): return f(value)
        case let .failure(error): return .fail(error)
        }
    }
}
func `catch`<T, E: Error>(_ f: @escaping (E) -> Signal<T, NoError>) -> (Signal<T, E>) -> Signal<T, NoError> {
    { signal in
        switch signal.result {
        case let .success(value): return .single(value)
        case let .failure(error): return f(error)
        }
    }
}
enum Events { static var values: [String] = [] }
struct MessageId { let id: Int32 }
struct AyuBurnSnapshotReference { let originalId: MessageId }
struct AyuVerifiedBurnSnapshot {
    let reference: AyuBurnSnapshotReference
    static var hashValid = true
    static func verify(_ reference: AyuBurnSnapshotReference, mediaBoxBasePath: String) -> AyuVerifiedBurnSnapshot? {
        Events.values.append("verify")
        return hashValid ? AyuVerifiedBurnSnapshot(reference: reference) : nil
    }
}
enum State { case committed, inFlight, burned }
final class Transaction {
    var state = State.committed
    var failConfirm = false
}
final class Postbox {
    let tx = Transaction()
    let mediaBox = MediaBox()
    func transaction<T>(_ f: (Transaction) -> T) -> Signal<T, NoError> { .single(f(tx)) }
}
final class MediaBox { let basePath = "test" }
enum Api {
    struct Request { let ids: [Int32] }
    enum functions { enum messages {
        static func readMessageContents(id: [Int32]) -> Request { Request(ids: id) }
    }}
    enum messages {
        struct Data { let pts: Int32; let ptsCount: Int32 }
        enum AffectedMessages { case affectedMessages(Data) }
    }
}
final class Network {
    var fails = false
    var onRequest: (() -> Void)?
    func request(_ request: Api.Request, automaticFloodWait: Bool) -> Signal<Api.messages.AffectedMessages, NetworkError> {
        Events.values.append("request")
        precondition(request.ids == [7] && !automaticFloodWait)
        onRequest?()
        return fails ? .fail(.disconnected) : .single(.affectedMessages(Api.messages.Data(pts: 3, ptsCount: 1)))
    }
}
enum UpdateGroup { case updatePts(pts: Int32, ptsCount: Int32) }
final class StateManager {
    func addUpdateGroups(_ updates: [UpdateGroup]) { Events.values.append("pts") }
}
final class Account {
    let postbox = Postbox()
    let network = Network()
    let stateManager = StateManager()
    var canPrepare = true
}
func ayuPrepareViewOnceSnapshot(account: Account, messageId: MessageId) -> Signal<AyuBurnSnapshotReference?, NoError> {
    Events.values.append("prepare")
    return .single(account.canPrepare ? AyuBurnSnapshotReference(originalId: messageId) : nil)
}
func ayuBeginPreparedViewOnceBurn(transaction: Transaction, verified: AyuVerifiedBurnSnapshot) -> Bool {
    Events.values.append("begin")
    guard transaction.state == .committed else { return false }
    transaction.state = .inFlight
    return true
}
func ayuConfirmPreparedViewOnceBurn(transaction: Transaction, reference: AyuBurnSnapshotReference) -> Bool {
    Events.values.append("confirm")
    if transaction.state == .burned { return true }
    guard transaction.state == .inFlight, !transaction.failConfirm else { return false }
    transaction.state = .burned
    return true
}
func markMessageContentAsConsumedRemotely(transaction: Transaction, messageId: MessageId, consumeDate: Int32?) {
    Events.values.append("local-expire")
}
'''
tests = r'''
let verified = AyuVerifiedBurnSnapshot(reference: AyuBurnSnapshotReference(originalId: MessageId(id: 7)))
func value(_ signal: Signal<AyuBurnAttemptResult, NoError>) -> AyuBurnAttemptResult {
    switch signal.result { case let .success(value): return value; case .failure: fatalError("unexpected") }
}
let ok = Account()
Events.values = []
precondition(value(ayuSendPreparedViewOnceBurn(account: ok, verified: verified)) == .acknowledged)
precondition(Events.values == ["begin", "request", "pts", "confirm", "local-expire"] && ok.postbox.tx.state == .burned)
Events.values = []
precondition(value(ayuSendPreparedViewOnceBurn(account: ok, verified: verified)) == .notStarted)
precondition(Events.values == ["begin"])

let disconnected = Account()
disconnected.network.fails = true
Events.values = []
precondition(value(ayuSendPreparedViewOnceBurn(account: disconnected, verified: verified)) == .uncertain)
precondition(Events.values == ["begin", "request"] && disconnected.postbox.tx.state == .inFlight)
Events.values = []
precondition(value(ayuSendPreparedViewOnceBurn(account: disconnected, verified: verified)) == .notStarted)
precondition(Events.values == ["begin"])

let failure = Account()
failure.postbox.tx.failConfirm = true
Events.values = []
precondition(value(ayuSendPreparedViewOnceBurn(account: failure, verified: verified)) == .uncertain)
precondition(Events.values == ["begin", "request", "pts", "confirm"] && failure.postbox.tx.state == .inFlight)

let raced = Account()
raced.network.onRequest = { _ = ayuConfirmPreparedViewOnceBurn(transaction: raced.postbox.tx, reference: verified.reference) }
Events.values = []
precondition(value(ayuSendPreparedViewOnceBurn(account: raced, verified: verified)) == .acknowledged)
precondition(Events.values == ["begin", "request", "confirm", "pts", "confirm", "local-expire"] && raced.postbox.tx.state == .burned)

let complete = Account()
Events.values = []
precondition(value(ayuAttemptViewOnceBurn(account: complete, messageId: MessageId(id: 7))) == .acknowledged)
precondition(Events.values == ["prepare", "verify", "begin", "request", "pts", "confirm", "local-expire"])
let unavailable = Account()
unavailable.canPrepare = false
Events.values = []
precondition(value(ayuAttemptViewOnceBurn(account: unavailable, messageId: MessageId(id: 7))) == .notStarted)
precondition(Events.values == ["prepare"])
AyuVerifiedBurnSnapshot.hashValid = false
let corrupted = Account()
Events.values = []
precondition(value(ayuAttemptViewOnceBurn(account: corrupted, messageId: MessageId(id: 7))) == .notStarted)
precondition(Events.values == ["prepare", "verify"] && corrupted.postbox.tx.state == .committed)
print("Burn request ordering passed with synchronous doubles; no network was contacted")
'''

with tempfile.TemporaryDirectory(prefix='ayu-burn-request-') as directory:
    directory = Path(directory)
    main = directory / 'main.swift'
    main.write_text(stubs + '\n\n' + production + '\n\n' + tests, encoding='utf-8')
    binary = directory / 'burn-request-tests'
    subprocess.run(['swiftc', '-warnings-as-errors', str(main), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
