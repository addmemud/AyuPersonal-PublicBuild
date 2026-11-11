"""Exercise actual presence manager initial state and transitions using network doubles."""
from pathlib import Path
import subprocess
import tempfile

source = Path("source/submodules/TelegramCore/Sources/State/ManagedAccountPresence.swift").read_text(encoding="utf-8")
start = source.index("private final class AccountPresenceManagerImpl")
opening = source.index("{", start)
depth = 0
for position in range(opening, len(source)):
    depth += (source[position] == "{") - (source[position] == "}")
    if depth == 0:
        logic = source[start:position + 1]
        break
else:
    raise ValueError("Unclosed presence manager")

stubs = r'''
import Foundation
enum NoError: Error {}
enum MTRpcError: Error { case failure }
class Disposable { func dispose() {} }
final class MetaDisposable: Disposable { func set(_ value: Disposable?) {} }
final class Queue { func isCurrent() -> Bool { true } }
final class SignalKitTimer {
    init(timeout: Double, repeat: Bool, completion: @escaping () -> Void, queue: Queue) {}
    func start() {}
    func invalidate() {}
}
struct Signal<T, E: Error> {
    let subscribe: (@escaping (T) -> Void, @escaping () -> Void) -> Void
    func start(next: @escaping (T) -> Void = { _ in }, completed: @escaping () -> Void = {}) -> Disposable {
        subscribe(next, completed); return Disposable()
    }
    static func single(_ value: T) -> Signal<T,E> { Signal { next, done in next(value); done() } }
}
final class ValuePromise<T> {
    private var value: T
    private var callback: ((T) -> Void)?
    init(_ value: T, ignoreRepeated: Bool) { self.value = value }
    func set(_ value: T) { self.value = value; callback?(value) }
    func get() -> Signal<T,NoError> { Signal { next, _ in self.callback = next; next(self.value) } }
}
infix operator |>: AdditionPrecedence
func |> <A,B>(value: A, transform: (A) -> B) -> B { transform(value) }
func distinctUntilChanged<T: Equatable,E: Error>(_ signal: Signal<T,E>) -> Signal<T,E> { signal }
func deliverOn<T,E: Error>(_ queue: Queue) -> (Signal<T,E>) -> Signal<T,E> { { $0 } }
func `catch`<T,E: Error>(_ f: @escaping (E) -> Signal<T,NoError>) -> (Signal<T,E>) -> Signal<T,NoError> {
    { signal in Signal<T,NoError> { next, done in signal.subscribe(next,done) } }
}
enum Api {
    enum Bool { case boolTrue, boolFalse }
    enum functions { enum account { static func updateStatus(offline: Api.Bool) -> Api.Bool { offline } } }
}
final class Network {
    var requests: [Api.Bool] = []
    func request(_ request: Api.Bool) -> Signal<Api.Bool,MTRpcError> { requests.append(request); return .single(.boolTrue) }
}
'''
checks = r'''
let offline = ValuePromise<Bool>(false, ignoreRepeated: true)
let network = Network()
private let manager = AccountPresenceManagerImpl(queue: Queue(), shouldKeepOnlinePresence: offline.get(), network: network)
precondition(network.requests == [.boolTrue], "initial offline must be sent")
offline.set(false)
precondition(network.requests == [.boolTrue], "unchanged state must not repeat")
offline.set(true)
offline.set(false)
precondition(network.requests == [.boolTrue, .boolFalse, .boolTrue], "online/offline transitions must be sent")
withExtendedLifetime(manager) {}
print("Initial offline and presence transitions send the expected requests")
'''
with tempfile.TemporaryDirectory() as temp:
    file = Path(temp) / "main.swift"
    file.write_text(stubs + "\n" + logic + "\n" + checks, encoding="utf-8")
    subprocess.run(["swiftc", str(file), "-o", str(Path(temp) / "test")], check=True)
    subprocess.run([str(Path(temp) / "test")], check=True)
