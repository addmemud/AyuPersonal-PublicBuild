"""Exercise the real SwiftSignalKit timeout and serial delivery with short timers."""
from pathlib import Path
import subprocess
import tempfile

source = Path('source/submodules/SSignalKit/SwiftSignalKit/Source')
names = ['Atomic', 'Disposable', 'Queue', 'Signal', 'Subscriber', 'Timer', 'Signal_Take']
sources = [source / (name + '.swift') for name in names]
if not all(path.is_file() for path in sources):
    raise RuntimeError('SwiftSignalKit source is missing')

def declaration(path, signature):
    text = path.read_text(encoding='utf-8')
    start = text.index(signature)
    opening = text.index('{', start)
    depth = 0
    for offset in range(opening, len(text)):
        depth += (text[offset] == '{') - (text[offset] == '}')
        if depth == 0:
            return text[start:offset + 1]
    raise ValueError('Unclosed Swift function')

main = r'''
import Foundation

func runCase(sourceDelay: Double, deadline: Double, expected: Int?) {
    let queue = Queue()
    let finished = DispatchSemaphore(value: 0)
    let values = Atomic<[Int]>(value: [])
    let signal = Signal<Int?, NoError> { subscriber in
        let work = DispatchWorkItem {
            subscriber.putNext(1)
            subscriber.putCompletion()
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + sourceDelay, execute: work)
        return ActionDisposable { work.cancel() }
    }
    let disposable = (signal
    |> deliverOn(queue)
    |> timeout(deadline, queue: queue, alternate: .single(nil)))
    .start(next: { value in
        let _ = values.modify { $0 + [value ?? 0] }
    }, completed: {
        finished.signal()
    })
    precondition(finished.wait(timeout: .now() + 3.0) == .success, "signal did not finish")
    Thread.sleep(forTimeInterval: max(sourceDelay, deadline) + 0.03)
    let observed = values.with { $0 }
    precondition(observed.count == 1, "timeout emitted more than one result")
    if let expected { precondition(observed == [expected], "unexpected timeout result") }
    disposable.dispose()
}

runCase(sourceDelay: 0.005, deadline: 0.05, expected: 1)
runCase(sourceDelay: 0.08, deadline: 0.01, expected: 0)
for _ in 0..<20 {
    runCase(sourceDelay: 0.01, deadline: 0.01, expected: nil)
}
print("Actual serial delivery and timeout emitted one result per attempt")
'''

with tempfile.TemporaryDirectory(prefix='ayu-real-timeout-') as directory:
    path = Path(directory)
    swift = path / 'main.swift'
    swift.write_text(main, encoding='utf-8')
    operators = path / 'operators.swift'
    operators.write_text('import Foundation\n' + declaration(source / 'Signal_Dispatch.swift', 'public func deliverOn<T, E>(_ queue: Queue)') + '\n' + declaration(source / 'Signal_Timing.swift', 'public func timeout<T, E>'), encoding='utf-8')
    binary = path / 'test'
    subprocess.run(['swiftc', '-swift-version', '5', *map(str, sources), str(operators), str(swift), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=30)
