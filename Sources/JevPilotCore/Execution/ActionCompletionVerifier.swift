// Verifies native actions by observing their expected desktop effect instead of sleeping.
import AppKit
import ApplicationServices
import Foundation

/// Allows the controller to test completion independently of native Accessibility effects.
@MainActor
public protocol ActionCompletionVerifying: AnyObject {
  func prepare(action: AutomationAction, before: DesktopState)
  func verify(action: AutomationAction, before: DesktopState, perception: DesktopPerceiving) async -> ExecutionResult
  func cancel()
}

extension ActionCompletionVerifying {
  public func prepare(action: AutomationAction, before: DesktopState) {}
  public func cancel() {}
}

/// The deterministic test default assumes fake executors report their own completion.
@MainActor
public final class ImmediateActionCompletionVerifier: ActionCompletionVerifying {
  public init() {}
  public func verify(action: AutomationAction, before: DesktopState, perception: DesktopPerceiving) async -> ExecutionResult {
    .init(succeeded: true, message: "Verified by executor.")
  }
}

/// Checks for a matching state change immediately, then polls briefly where apps omit AX events.
@MainActor
public final class DesktopActionCompletionVerifier: ActionCompletionVerifying {
  private var observation: DesktopChangeObservation?
  private let timeout: Duration
  public init(timeout: Duration = .seconds(3)) { self.timeout = timeout }

  public func prepare(action: AutomationAction, before: DesktopState) {
    observation?.stop()
    observation = DesktopChangeObservation(processIdentifier: before.activeApplication?.processIdentifier)
  }

  public func cancel() { observation?.stop(); observation = nil }

  public func verify(action: AutomationAction, before: DesktopState, perception: DesktopPerceiving) async -> ExecutionResult {
    defer { cancel() }
    let deadline = ContinuousClock.now.advanced(by: timeout)
    while ContinuousClock.now < deadline {
      if Task.isCancelled { return .init(succeeded: false, message: "Run stopped.") }
      if let result = inspect(action: action, before: before, perception: perception) { return result }
      if let observation { await observation.waitForChangeOrPoll() }
      else {
        do { try await Task.sleep(for: .milliseconds(50)) }
        catch { return .init(succeeded: false, message: "Run stopped.") }
      }
    }
    return .init(succeeded: false, message: "Could not verify \(action.summary) on screen.")
  }

  private func inspect(action: AutomationAction, before: DesktopState, perception: DesktopPerceiving) -> ExecutionResult? {
    if case .openApp(let bundleID, _) = action {
      return NSWorkspace.shared.frontmostApplication?.bundleIdentifier == bundleID
        ? .init(succeeded: true, message: "App is frontmost.") : nil
    }
    if case .focusApp(let bundleID, _) = action {
      return NSWorkspace.shared.frontmostApplication?.bundleIdentifier == bundleID
        ? .init(succeeded: true, message: "App is frontmost.") : nil
    }
    switch action {
    case .finderRenameItem(_, let path, let name):
      let renamed = URL(fileURLWithPath: path).deletingLastPathComponent().appendingPathComponent(name).path
      if !FileManager.default.fileExists(atPath: path) && FileManager.default.fileExists(atPath: renamed) {
        return .init(succeeded: true, message: "Finder rename verified.")
      }
    case .finderCopyItem(_, let path, let destination), .finderMoveItem(_, let path, let destination):
      let target = URL(fileURLWithPath: destination).appendingPathComponent(URL(fileURLWithPath: path).lastPathComponent).path
      let moving: Bool
      if case .finderMoveItem = action { moving = true } else { moving = false }
      if FileManager.default.fileExists(atPath: target), moving ? !FileManager.default.fileExists(atPath: path) : FileManager.default.fileExists(atPath: path) {
        return .init(succeeded: true, message: moving ? "Finder move verified." : "Finder copy verified.")
      }
    default: break
    }
    guard let after = try? perception.snapshot(recentActions: before.recentActions) else { return nil }
    let changed = after.focusedWindowID != before.focusedWindowID
      || after.focusedElementID != before.focusedElementID
      || after.windows != before.windows || after.elements != before.elements
    switch action {
    case .focusElement(let id, _):
      return after.focusedElementID == id ? .init(succeeded: true, message: "Target is focused.") : nil
    case .typeText(let id, let text):
      let oldValue = before.elements.first(where: { $0.id == id })?.value ?? ""
      guard let newValue = after.elements.first(where: { $0.id == id })?.value,
        newValue != oldValue, newValue.contains(text)
      else { return nil }
      return .init(succeeded: true, message: "Text appeared in the target field.")
    case .closeWindow(let id, _):
      return !after.windows.contains(where: { $0.id == id })
        ? .init(succeeded: true, message: "Window closed.") : nil
    case .minimizeWindow(let id, _):
      return after.windows.first(where: { $0.id == id })?.isMinimized == true
        ? .init(succeeded: true, message: "Window minimized.") : nil
    case .restoreWindow(let id, _):
      return after.windows.first(where: { $0.id == id })?.isMinimized == false
        ? .init(succeeded: true, message: "Window restored.") : nil
    case .enterFullScreen(let id, _):
      return after.windows.first(where: { $0.id == id })?.isFullScreen == true
        ? .init(succeeded: true, message: "Fullscreen entered.") : nil
    case .exitFullScreen(let id, _):
      return after.windows.first(where: { $0.id == id })?.isFullScreen == false
        ? .init(succeeded: true, message: "Fullscreen exited.") : nil
    case .finderOpenFolder(let path):
      return after.activeApplication?.bundleIdentifier == "com.apple.finder"
        && after.windows.contains(where: { $0.isFocused && ($0.url == path || $0.title == URL(fileURLWithPath: path).lastPathComponent) })
        ? .init(succeeded: true, message: "Finder location verified.") : nil
    case .finderSelectItem(let id, _):
      return after.elements.first(where: { $0.id == id })?.isSelected == true
        ? .init(succeeded: true, message: "Finder selection verified.") : nil
    case .finderOpenItem(_, let path):
      let itemName = URL(fileURLWithPath: path).lastPathComponent
      let stem = URL(fileURLWithPath: path).deletingPathExtension().lastPathComponent
      return after.windows.contains(where: { window in
        window.isFocused && (window.url == path || window.title == itemName
          || window.title == stem || (stem.count >= 3 && window.title?.hasPrefix(stem + " —") == true))
      })
        ? .init(succeeded: true, message: "Finder item opened.") : nil
    case .activateMenu, .selectTab, .clickElement, .pressKey, .scrollUp, .scrollDown,
      .nextTab, .previousTab, .navigateBack, .navigateForward:
      return changed ? .init(succeeded: true, message: "Desktop change observed.") : nil
    case .searchInApp(let query):
      return after.elements.contains(where: { $0.isFocused && ($0.value?.contains(query) == true) })
        ? .init(succeeded: true, message: "Search query appeared.") : nil
    case .terminalType(let command), .terminalRun(let command):
      return after.elements.contains(where: { $0.isFocused && $0.value?.contains(command) == true })
        ? .init(succeeded: true, message: "Terminal accepted the command text; exit status is unknown.") : nil
    case .finderRenameItem, .finderCopyItem, .finderMoveItem:
      return nil
    case .stop:
      return .init(succeeded: true, message: "Stopped.")
    default:
      return changed ? .init(succeeded: true, message: "Desktop changed as expected.") : nil
    }
  }
}

/// AX and workspace notifications signal likely changes; verification still reads fresh state.
@MainActor
private final class DesktopChangeObservation {
  private var observer: AXObserver?
  private var workspaceToken: NSObjectProtocol?
  private var runLoopSource: CFRunLoopSource?

  init(processIdentifier: Int32?) {
    workspaceToken = NSWorkspace.shared.notificationCenter.addObserver(
      forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
    ) { _ in ObservationPulse.shared.mark() }
    guard let processIdentifier, processIdentifier != ProcessInfo.processInfo.processIdentifier else { return }
    var created: AXObserver?
    guard AXObserverCreate(processIdentifier, { _, _, _, _ in ObservationPulse.shared.mark() }, &created) == .success,
      let created else { return }
    let app = AXUIElementCreateApplication(processIdentifier)
    for name in [kAXFocusedWindowChangedNotification, kAXFocusedUIElementChangedNotification,
      kAXWindowCreatedNotification, kAXWindowMiniaturizedNotification,
      kAXWindowDeminiaturizedNotification, kAXValueChangedNotification,
      kAXSelectedChildrenChangedNotification] {
      _ = AXObserverAddNotification(created, app, name as CFString, nil)
    }
    let source = AXObserverGetRunLoopSource(created)
    CFRunLoopAddSource(CFRunLoopGetMain(), source, .defaultMode)
    observer = created
    runLoopSource = source
  }

  func stop() {
    if let source = runLoopSource { CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .defaultMode) }
    if let token = workspaceToken { NSWorkspace.shared.notificationCenter.removeObserver(token) }
    runLoopSource = nil
    workspaceToken = nil
    observer = nil
    ObservationPulse.shared.mark()
  }

  func waitForChangeOrPoll() async { await ObservationPulse.shared.wait() }
}

/// A one-shot wake-up handles notifications without blocking the main actor.
private final class ObservationPulse: @unchecked Sendable {
  static let shared = ObservationPulse()
  private let lock = NSLock()
  private var waiter: Waiter?

  func mark() {
    let current = lock.withLock { let old = waiter; waiter = nil; return old }
    current?.resume()
  }

  func wait() async {
    await withCheckedContinuation { continuation in
      let next = Waiter(continuation)
      lock.withLock { waiter = next }
      Task.detached {
        try? await Task.sleep(for: .milliseconds(50))
        next.resume()
      }
    }
  }

  private final class Waiter: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Void, Never>?
    init(_ continuation: CheckedContinuation<Void, Never>) { self.continuation = continuation }
    func resume() {
      let value = lock.withLock { let old = continuation; continuation = nil; return old }
      value?.resume()
    }
  }
}
