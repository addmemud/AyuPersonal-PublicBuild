"""Exercise the real interactive consumption guard with a synchronous Postbox double.

This does not exercise actual networking, asynchronous scheduling or an iPhone.
Run from a CI checkout root with the patched Telegram checkout in ./source.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path("source")


def declaration(path: Path, signature: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(signature)
    opening = text.index("{", start)
    depth = 0
    for position in range(opening, len(text)):
        if text[position] == "{":
            depth += 1
        elif text[position] == "}":
            depth -= 1
            if depth == 0:
                return text[start:position + 1]
    raise ValueError("Unclosed declaration: " + signature)


settings = root / "submodules/TelegramCore/Sources/Settings/GhostModeSettings.swift"
consumption = root / "submodules/TelegramCore/Sources/TelegramEngine/Messages/MarkMessageContentAsConsumedInteractively.swift"
logic = "\n\n".join([
    declaration(settings, "public struct GhostModeSettings:"),
    declaration(settings, "public func ghostModeDefersViewOnceConsumption("),
    declaration(consumption, "private func messageIsViewOnce("),
    declaration(consumption, "func _internal_markMessageContentAsConsumedInteractively("),
])

stubs = r'''
import Foundation
import CoreFoundation

enum NoError: Error {}
struct Signal<Value, Failure: Error> { let value: Value }
enum Namespaces {
    enum Peer { static let CloudUser: Int32 = 1; static let CloudGroup: Int32 = 2; static let SecretChat: Int32 = 3 }
    enum Message { static let Cloud: Int32 = 0 }
}
struct PeerId: Equatable { let namespace: Int32 }
struct MessageId: Equatable { let peerId: PeerId; let namespace: Int32; let id: Int32 }
struct MessageFlags: OptionSet {
    let rawValue: Int
    static let Incoming = MessageFlags(rawValue: 1)
}
protocol MessageAttribute {}
struct ConsumableContentMessageAttribute: MessageAttribute { let consumed: Bool }
struct ConsumablePersonalMentionMessageAttribute: MessageAttribute { let consumed: Bool; let pending: Bool }
struct AutoremoveTimeoutMessageAttribute: MessageAttribute { let timeout: Int32; let countdownBeginTime: Int32? }
struct AutoclearTimeoutMessageAttribute: MessageAttribute { let timeout: Int32; let countdownBeginTime: Int32? }
let viewOnceTimeout = Int32.max
struct Peer { let id: PeerId }
struct ForwardInfo {
    let author: Peer? = nil; let source: Peer? = nil; let sourceMessageId: MessageId? = nil
    let date: Int32 = 0; let authorSignature: String? = nil; let psaType: String? = nil; let flags = 0
}
struct StoreMessageForwardInfo {
    init(authorId: PeerId?, sourceId: PeerId?, sourceMessageId: MessageId?, date: Int32,
         authorSignature: String?, psaType: String?, flags: Int) {}
}
struct StoreMessageFlags { init(_ flags: MessageFlags) {} }
struct Message {
    let id: MessageId
    let flags: MessageFlags
    var attributes: [MessageAttribute]
    let globallyUniqueId: Int64? = nil
    let groupingKey: Int64? = nil
    let threadId: Int64? = nil
    let timestamp: Int32 = 0
    let tags = 0; let globalTags = 0; let localTags = 0
    let forwardInfo: ForwardInfo? = nil
    let author: Peer? = nil
    let text = ""; let media: [Int] = []
    let secretMediaDuration: Double? = nil
}
struct StoreMessage {
    let attributes: [MessageAttribute]
    init(id: MessageId, customStableId: Int32?, globallyUniqueId: Int64?, groupingKey: Int64?, threadId: Int64?,
         timestamp: Int32, flags: StoreMessageFlags, tags: Int, globalTags: Int, localTags: Int,
         forwardInfo: StoreMessageForwardInfo?, authorId: PeerId?, text: String,
         attributes: [MessageAttribute], media: [Int]) { self.attributes = attributes }
}
enum MessageUpdate { case update(StoreMessage) }
enum SecretChatLayer { case layer8 }
struct ActiveLayer { let secretChatLayer: SecretChatLayer = .layer8 }
struct LayerNegotiationState { let activeLayer = ActiveLayer() }
struct SequenceState { let layerNegotiationState = LayerNegotiationState() }
enum SecretEmbeddedState { case terminated, handshake, basicLayer, sequenceBasedLayer(SequenceState) }
struct SecretChatState: Equatable {
    let embeddedState: SecretEmbeddedState = .terminated
    static func == (lhs: Self, rhs: Self) -> Bool { true }
}
enum SecretChatOutgoingOperationContents {
    case readMessagesContent(layer: SecretChatLayer, actionGloballyUniqueId: Int64, globallyUniqueIds: [Int64])
}
enum PendingMessageActionType { case consumeUnseenPersonalMessage }
struct ConsumePersonalMessageAction {}
final class Transaction {
    var message: Message
    var settings: GhostModeSettings
    var acknowledgements = 0
    var pendingActions = 0
    init(message: Message, settings: GhostModeSettings) { self.message = message; self.settings = settings }
    func getMessage(_ id: MessageId) -> Message? { id == message.id ? message : nil }
    func getPeerChatState(_ id: PeerId) -> Any? { nil }
    func setPeerChatState(_ id: PeerId, state: SecretChatState) {}
    func setPendingMessageAction(type: PendingMessageActionType, id: MessageId, action: ConsumePersonalMessageAction?) { pendingActions += 1 }
    func updateMessage(_ id: MessageId, update: (Message) -> MessageUpdate) {
        switch update(message) { case let .update(stored): message.attributes = stored.attributes }
    }
}
final class Postbox {
    let tx: Transaction
    init(_ tx: Transaction) { self.tx = tx }
    func transaction<T>(_ body: (Transaction) -> T) -> Signal<T, NoError> { Signal(value: body(tx)) }
}
func ghostModeSettings(transaction: Transaction) -> GhostModeSettings { transaction.settings }
func addSynchronizeConsumeMessageContentsOperation(transaction: Transaction, messageIds: [MessageId]) { transaction.acknowledgements += 1 }
func addSecretChatOutgoingOperation(transaction: Transaction, peerId: PeerId,
                                    operation: SecretChatOutgoingOperationContents, state: SecretChatState) -> SecretChatState { state }
'''

tests = r'''
func runCase(keep: Bool, incoming: Bool = true, peerNamespace: Int32 = Namespaces.Peer.CloudUser,
             timeout: Int32 = viewOnceTimeout, force: Bool = false, ghost: Bool = false) -> Transaction {
    let id = MessageId(peerId: PeerId(namespace: peerNamespace), namespace: Namespaces.Message.Cloud, id: 42)
    let attrs: [MessageAttribute] = [ConsumableContentMessageAttribute(consumed: false),
                                     AutoclearTimeoutMessageAttribute(timeout: timeout, countdownBeginTime: nil)]
    var settings = GhostModeSettings.defaultSettings
    settings.keepViewOnceMedia = keep
    settings.isEnabled = ghost
    let tx = Transaction(message: Message(id: id, flags: incoming ? [.Incoming] : [], attributes: attrs), settings: settings)
    _ = _internal_markMessageContentAsConsumedInteractively(postbox: Postbox(tx), messageId: id, force: force)
    return tx
}
func consumed(_ tx: Transaction) -> Bool {
    (tx.message.attributes.first { $0 is ConsumableContentMessageAttribute } as! ConsumableContentMessageAttribute).consumed
}
func countdownStarted(_ tx: Transaction) -> Bool {
    (tx.message.attributes.first { $0 is AutoclearTimeoutMessageAttribute } as! AutoclearTimeoutMessageAttribute).countdownBeginTime != nil
}
let retained = runCase(keep: true)
precondition(!consumed(retained) && !countdownStarted(retained) && retained.acknowledgements == 0)
let retainedWhileGhost = runCase(keep: true, ghost: true)
precondition(!consumed(retainedWhileGhost) && !countdownStarted(retainedWhileGhost) && retainedWhileGhost.acknowledgements == 0)
let ordinary = runCase(keep: false)
precondition(consumed(ordinary) && countdownStarted(ordinary) && ordinary.acknowledgements == 1)
let forced = runCase(keep: true, force: true, ghost: true)
precondition(consumed(forced) && countdownStarted(forced) && forced.acknowledgements == 1)
let timed = runCase(keep: true, timeout: 60)
precondition(consumed(timed) && countdownStarted(timed) && timed.acknowledgements == 1)
let otherChat = runCase(keep: true, peerNamespace: Namespaces.Peer.CloudGroup)
precondition(consumed(otherChat) && countdownStarted(otherChat) && otherChat.acknowledgements == 1)
let secret = runCase(keep: true, peerNamespace: Namespaces.Peer.SecretChat)
precondition(consumed(secret) && countdownStarted(secret))
let outgoing = runCase(keep: true, incoming: false)
precondition(!consumed(outgoing) && !countdownStarted(outgoing) && outgoing.acknowledgements == 0)
print("Interactive view-once consumption guards passed with synchronous Postbox doubles")
'''

with tempfile.TemporaryDirectory(prefix="ayu-viewonce-") as temp:
    directory = Path(temp)
    main = directory / "main.swift"
    main.write_text(stubs + "\n" + logic + "\n" + tests, encoding="utf-8")
    binary = directory / "viewonce-tests"
    subprocess.run(["swiftc", "-warnings-as-errors", str(main), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
