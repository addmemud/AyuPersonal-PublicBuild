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
        if source[i] == '{': depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0: return source[position:i + 1]
    raise ValueError(f'Unclosed declaration: {start}')

retention = declaration('submodules/TelegramCore/Sources/TelegramEngine/Messages/DeletedMessageArchive.swift', 'public func deletedMessageArchiveRetentionPlan')
ghost = declaration('submodules/TelegramCore/Sources/Settings/GhostModeSettings.swift', 'public struct GhostModeSettings')
archive_settings = declaration('submodules/TelegramCore/Sources/Settings/DeletedMessageArchiveSettings.swift', 'public struct DeletedMessageArchiveSettings')
defer = declaration('submodules/TelegramCore/Sources/Settings/GhostModeSettings.swift', 'public func ghostModeDefersViewOnceConsumption')
replay = declaration('submodules/TelegramCore/Sources/Settings/GhostModeSettings.swift', 'public func ghostModeAllowsViewOnceReplay')
pending_read = declaration('submodules/TelegramCore/Sources/TelegramEngine/Messages/InstallInteractiveReadMessagesAction.swift', 'func ayuDefersViewOnceReadAction')
read_range = declaration('submodules/TelegramCore/Sources/AyuPrivacy/AyuReadObservations.swift', 'func ayuReadRangeContains')
stubs = r'''
enum Namespaces {
    enum Peer { static let CloudUser: Int32 = 1 }
    enum Message { static let Cloud: Int32 = 2 }
}
struct PeerId { let namespace: Int32 }
struct MessageId { let peerId: PeerId; let namespace: Int32 }
protocol MessageAttribute {}
struct AutoremoveTimeoutMessageAttribute: MessageAttribute { let timeout: Int32 }
struct AutoclearTimeoutMessageAttribute: MessageAttribute { let timeout: Int32 }
let viewOnceTimeout: Int32 = Int32.max
'''
test = r'''
let entries: [(id: String, size: Int64, isDisappearing: Bool)] = [
    ("old", 5, false), ("big", 60, false), ("new", 5, false)
]
let plan = deletedMessageArchiveRetentionPlan(items: entries, limitBytes: 50, disappearingLimitBytes: 50)
precondition(plan.removed == ["old", "big"] && plan.retained == ["new"])
let once: [(id: String, size: Int64, isDisappearing: Bool)] = [
    ("ordinary", 10, false), ("video", 80, true), ("photo", 5, true)
]
let constrained = deletedMessageArchiveRetentionPlan(items: once, limitBytes: 500, disappearingLimitBytes: 50)
precondition(constrained.removed == ["video"] && constrained.retained == ["ordinary", "photo"])
var settings = GhostModeSettings.defaultSettings
precondition(!ghostModeDefersViewOnceConsumption(settings: settings, isViewOnce: true, isSecretChat: false, force: false))
settings.isEnabled = true
settings.keepViewOnceMedia = true
precondition(ghostModeDefersViewOnceConsumption(settings: settings, isViewOnce: true, isSecretChat: false, force: false))
settings.isEnabled = false
precondition(ghostModeDefersViewOnceConsumption(settings: settings, isViewOnce: true, isSecretChat: false, force: false))
precondition(!ghostModeDefersViewOnceConsumption(settings: settings, isViewOnce: true, isSecretChat: true, force: false))
precondition(!ghostModeDefersViewOnceConsumption(settings: settings, isViewOnce: true, isSecretChat: false, force: true))
precondition(!ghostModeDefersViewOnceConsumption(settings: settings, isViewOnce: false, isSecretChat: false, force: false))
precondition(ghostModeAllowsViewOnceReplay(isViewOnce: true, isIncoming: true, isCloudUser: true, enabled: true))
precondition(!ghostModeAllowsViewOnceReplay(isViewOnce: false, isIncoming: true, isCloudUser: true, enabled: true))
precondition(!ghostModeAllowsViewOnceReplay(isViewOnce: true, isIncoming: false, isCloudUser: true, enabled: true))
precondition(!ghostModeAllowsViewOnceReplay(isViewOnce: true, isIncoming: true, isCloudUser: false, enabled: true))
precondition(!ghostModeAllowsViewOnceReplay(isViewOnce: true, isIncoming: true, isCloudUser: true, enabled: false))
let cloudMessage = MessageId(peerId: PeerId(namespace: Namespaces.Peer.CloudUser), namespace: Namespaces.Message.Cloud)
let otherChatMessage = MessageId(peerId: PeerId(namespace: 99), namespace: Namespaces.Message.Cloud)
let oneTime: [MessageAttribute] = [AutoclearTimeoutMessageAttribute(timeout: viewOnceTimeout)]
let ordinary: [MessageAttribute] = [AutoclearTimeoutMessageAttribute(timeout: 60)]
precondition(ayuDefersViewOnceReadAction(id: cloudMessage, isIncoming: true, attributes: oneTime, settings: settings))
precondition(!ayuDefersViewOnceReadAction(id: cloudMessage, isIncoming: false, attributes: oneTime, settings: settings))
precondition(!ayuDefersViewOnceReadAction(id: cloudMessage, isIncoming: true, attributes: ordinary, settings: settings))
precondition(!ayuDefersViewOnceReadAction(id: otherChatMessage, isIncoming: true, attributes: oneTime, settings: settings))
precondition(!ayuReadRangeContains(afterId: 10, throughId: 15, messageId: 10))
precondition(ayuReadRangeContains(afterId: 10, throughId: 15, messageId: 11))
precondition(ayuReadRangeContains(afterId: 10, throughId: 15, messageId: 15))
precondition(!ayuReadRangeContains(afterId: 10, throughId: 15, messageId: 16))
let data = try JSONEncoder().encode(settings)
let restored = try JSONDecoder().decode(GhostModeSettings.self, from: data)
precondition(restored == settings)
var archiveSettings = DeletedMessageArchiveSettings.defaultSettings
precondition(archiveSettings.mediaLimitBytes == 5 * 1024 * 1024 * 1024)
archiveSettings.mediaLimitGigabytes = DeletedMessageArchiveSettings.limit300Megabytes
precondition(archiveSettings.mediaLimitBytes == 300 * 1024 * 1024)
archiveSettings.disappearingMediaLimitGigabytes = DeletedMessageArchiveSettings.unlimitedMediaLimit
precondition(archiveSettings.disappearingMediaLimitBytes == Int64.max)
let archiveData = try JSONEncoder().encode(archiveSettings)
let restoredArchive = try JSONDecoder().decode(DeletedMessageArchiveSettings.self, from: archiveData)
precondition(restoredArchive == archiveSettings && restoredArchive.mediaLimitBytes == 300 * 1024 * 1024)
precondition(DeletedMessageArchiveSettings.bytesForMediaLimit(-2) == nil)
print("Retention and ghost policy checks passed")
'''
with tempfile.TemporaryDirectory(prefix='ayu-extra-policy-') as directory:
    directory = Path(directory)
    swift = directory / 'main.swift'
    swift.write_text('import Foundation\n\n' + stubs + '\n\n' + retention + '\n\n' + ghost + '\n\n' + archive_settings + '\n\n' + defer + '\n\n' + replay + '\n\n' + pending_read + '\n\n' + read_range + '\n' + test, encoding='utf-8')
    binary = directory / 'policy-tests'
    subprocess.run(['swiftc', '-warnings-as-errors', str(swift), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
