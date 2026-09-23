// Executes approved actions through AppKit, Accessibility, and CoreGraphics.
import AppKit
import ApplicationServices
import Foundation

/// Contains the system's only direct macOS desktop side effects.
@MainActor
public final class MacOSActionExecutor: ActionExecuting {
  private unowned let perception: AccessibilityPerception

  public init(perception: AccessibilityPerception) {
    self.perception = perception
  }

  /// Resolves and performs one concrete action, failing safely for stale UI targets.
  public func execute(_ action: AutomationAction) async -> ExecutionResult {
    guard !Task.isCancelled else { return .init(succeeded: false, message: "Run stopped.") }
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == perception.observedProcessIdentifier else {
      return .init(succeeded: false, message: "The active app changed. Start a new command.")
    }
    switch action {
    case .openApp(let bundleIdentifier, let name):
      guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleIdentifier)
      else {
        return .init(succeeded: false, message: "\(name) is not installed.")
      }
      return await activateApplication(at: url, bundleIdentifier: bundleIdentifier, name: name, successVerb: "Opened", failureVerb: "open")

    case .focusApp(let bundleIdentifier, let name):
      guard
        let application = NSRunningApplication.runningApplications(
          withBundleIdentifier: bundleIdentifier
        ).first(where: { !$0.isTerminated }),
        let url = application.bundleURL ?? NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleIdentifier)
      else {
        return .init(succeeded: false, message: "\(name) is no longer running.")
      }
      // Workspace requests foreground activation even though Jev Pilot has restored the
      // previously reviewed app and is no longer the frontmost process.
      return await activateApplication(at: url, bundleIdentifier: bundleIdentifier, name: name, successVerb: "Focused", failureVerb: "focus")

    case .closeWindow(let windowID, let title):
      guard let window = perception.element(for: windowID),
        let closeButton = elementAttribute(window, kAXCloseButtonAttribute)
      else {
        return .init(
          succeeded: false, message: "The focused window no longer has an accessible close button.")
      }
      return perform(kAXPressAction, on: closeButton, success: "Closed \(title ?? "window").")

    case .minimizeWindow(let id, _), .restoreWindow(let id, _):
      guard let window = perception.element(for: id) else { return staleElementResult }
      let minimized: Bool
      if case .minimizeWindow = action { minimized = true } else { minimized = false }
      return axResult(AXUIElementSetAttributeValue(window, kAXMinimizedAttribute as CFString, minimized ? kCFBooleanTrue : kCFBooleanFalse), success: minimized ? "Window minimized." : "Window restored.")

    case .enterFullScreen(let id, _), .exitFullScreen(let id, _):
      guard let window = perception.element(for: id),
        let button = elementAttribute(window, kAXFullScreenButtonAttribute)
      else { return .init(succeeded: false, message: "This window does not expose a fullscreen control.") }
      return perform(kAXPressAction, on: button, success: "Fullscreen control pressed.")

    case .clickElement(let elementID, let label):
      guard let element = perception.element(for: elementID) else {
        return staleElementResult
      }
      return perform(kAXPressAction, on: element, success: "Clicked \(label ?? "element").")

    case .focusElement(let elementID, let label):
      guard let element = perception.element(for: elementID) else {
        return staleElementResult
      }
      let result = AXUIElementSetAttributeValue(
        element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
      return axResult(result, success: "Focused \(label ?? "element").")

    case .typeText(let elementID, let text):
      guard let element = perception.element(for: elementID) else { return staleElementResult }
      guard boolAttribute(element, kAXFocusedAttribute) == true else {
        return .init(
          succeeded: false,
          message: "The intended text field is no longer focused. A fresh snapshot is required."
        )
      }
      return typeUnicode(text)

    case .activateMenu(let id, _), .selectTab(let id, _):
      guard let element = perception.element(for: id) else { return staleElementResult }
      return perform(kAXPressAction, on: element, success: "Control activated.")

    case .searchInApp(let query):
      guard postChord(keyCode: 3, modifiers: .maskCommand) else { return .init(succeeded: false, message: "Could not open search.") }
      guard await waitForFocusedTextField() else { return .init(succeeded: false, message: "The app did not open a search field.") }
      return typeUnicode(query)

    case .nextTab:
      return postChord(keyCode: 30, modifiers: [.maskCommand, .maskShift])
        ? .init(succeeded: true, message: "Next tab requested.")
        : .init(succeeded: false, message: "Could not select the next tab.")
    case .previousTab:
      return postChord(keyCode: 33, modifiers: [.maskCommand, .maskShift])
        ? .init(succeeded: true, message: "Previous tab requested.")
        : .init(succeeded: false, message: "Could not select the previous tab.")
    case .navigateBack:
      return postChord(keyCode: 33, modifiers: .maskCommand)
        ? .init(succeeded: true, message: "Back navigation requested.")
        : .init(succeeded: false, message: "Could not navigate back.")
    case .navigateForward:
      return postChord(keyCode: 30, modifiers: .maskCommand)
        ? .init(succeeded: true, message: "Forward navigation requested.")
        : .init(succeeded: false, message: "Could not navigate forward.")

    case .finderOpenFolder(let path):
      return await openFinderFolder(path)

    case .finderSelectItem(let id, _):
      guard let element = perception.element(for: id) else { return staleElementResult }
      return axResult(AXUIElementSetAttributeValue(element, kAXSelectedAttribute as CFString, kCFBooleanTrue), success: "Finder item selected.")

    case .finderOpenItem(let id, _):
      guard let element = perception.element(for: id) else { return staleElementResult }
      _ = AXUIElementSetAttributeValue(element, kAXSelectedAttribute as CFString, kCFBooleanTrue)
      guard postChord(keyCode: 31, modifiers: .maskCommand) else { return .init(succeeded: false, message: "Could not open the Finder item.") }
      return .init(succeeded: true, message: "Finder open requested.")

    case .finderRenameItem(let id, let path, let name):
      guard !name.isEmpty, name != ".", name != "..", !name.contains("/"),
        !name.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
        return .init(succeeded: false, message: "The new file name is invalid.")
      }
      let renamed = URL(fileURLWithPath: path).deletingLastPathComponent().appendingPathComponent(name).path
      guard !FileManager.default.fileExists(atPath: renamed) else {
        return .init(succeeded: false, message: "An item with that name already exists.")
      }
      guard let element = perception.element(for: id) else { return staleElementResult }
      guard AXUIElementSetAttributeValue(element, kAXSelectedAttribute as CFString, kCFBooleanTrue) == .success,
        postChord(keyCode: 36)
      else { return .init(succeeded: false, message: "Finder could not rename the selected item.") }
      guard await waitForFocusedTextField(), typeUnicode(name).succeeded, postChord(keyCode: 36) else {
        return .init(succeeded: false, message: "Finder did not open an editable file name.")
      }
      return .init(succeeded: true, message: "Finder rename requested.")

    case .finderCopyItem(let id, let path, let destination), .finderMoveItem(let id, let path, let destination):
      let target = URL(fileURLWithPath: destination).appendingPathComponent(URL(fileURLWithPath: path).lastPathComponent).path
      guard FileManager.default.fileExists(atPath: path), !FileManager.default.fileExists(atPath: target) else {
        return .init(succeeded: false, message: "Source is missing or the destination already contains this item.")
      }
      guard let element = perception.element(for: id) else { return staleElementResult }
      guard AXUIElementSetAttributeValue(element, kAXSelectedAttribute as CFString, kCFBooleanTrue) == .success,
        postChord(keyCode: 8, modifiers: .maskCommand)
      else { return .init(succeeded: false, message: "Finder could not copy the selected item.") }
      let opened = await openFinderFolder(destination)
      guard opened.succeeded, NSWorkspace.shared.frontmostApplication?.bundleIdentifier == "com.apple.finder" else { return opened }
      let moving: Bool
      if case .finderMoveItem = action { moving = true } else { moving = false }
      guard postChord(keyCode: 9, modifiers: moving ? [.maskCommand, .maskAlternate] : .maskCommand) else {
        return .init(succeeded: false, message: "Finder could not paste into the destination.")
      }
      return .init(succeeded: true, message: moving ? "Finder move requested." : "Finder copy requested.")

    case .terminalType(let command), .terminalRun(let command):
      guard NSWorkspace.shared.frontmostApplication?.bundleIdentifier == "com.apple.Terminal" else {
        return .init(succeeded: false, message: "Terminal is no longer active.")
      }
      guard !command.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
        return .init(succeeded: false, message: "Terminal commands must be one line without control characters.")
      }
      let typed = typeUnicode(command)
      guard typed.succeeded else { return typed }
      if case .terminalRun = action {
        guard let entered = await waitForTerminalText(command) else {
          return .init(succeeded: false, message: "Could not verify command entry, so Return was not sent.")
        }
        guard postChord(keyCode: 36) else { return .init(succeeded: false, message: "Could not submit the Terminal command.") }
        guard await waitForTerminalChange(after: entered) else {
          return .init(succeeded: false, message: "Return was sent, but Terminal submission could not be verified; exit status is unknown.")
        }
        return .init(succeeded: true, message: "Command submitted to Terminal; exit status is unknown.")
      }
      return .init(succeeded: true, message: "Command typed in Terminal without running it.")

    case .pressKey(let key):
      guard let code = keyCode(for: key) else {
        return .init(succeeded: false, message: "Unsupported key \(key.rawValue).")
      }
      guard let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true),
        let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)
      else {
        return .init(succeeded: false, message: "Could not create keyboard event.")
      }
      down.post(tap: .cghidEventTap)
      up.post(tap: .cghidEventTap)
      return .init(succeeded: true, message: "Pressed \(key.rawValue).")

    case .scrollUp:
      return scroll(lines: 5, message: "Scrolled up.")
    case .scrollDown:
      return scroll(lines: -5, message: "Scrolled down.")
    case .stop(let reason):
      return .init(succeeded: true, message: reason)
    }
  }

  /// A successful launch request is not enough: wait until the chosen app is actually frontmost.
  private func activateApplication(
    at url: URL, bundleIdentifier: String, name: String,
    successVerb: String, failureVerb: String
  ) async -> ExecutionResult {
    let configuration = NSWorkspace.OpenConfiguration()
    configuration.activates = true
    configuration.allowsRunningApplicationSubstitution = true
    do {
      let application = try await NSWorkspace.shared.openApplication(at: url, configuration: configuration)
      guard !Task.isCancelled else { return .init(succeeded: false, message: "Run stopped.") }
      guard application.bundleIdentifier == bundleIdentifier else {
        return .init(succeeded: false, message: "macOS opened a different app instead of \(name).")
      }
      for _ in 0..<60 {
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == application.processIdentifier {
          return .init(succeeded: true, message: "\(successVerb) \(name).")
        }
        do { try await Task.sleep(for: .milliseconds(50)) }
        catch { return .init(succeeded: false, message: "Run stopped.") }
      }
      return .init(succeeded: false, message: "macOS did not bring \(name) to the foreground.")
    } catch {
      return .init(succeeded: false, message: "Could not \(failureVerb) \(name): \(error.localizedDescription)")
    }
  }

  private var staleElementResult: ExecutionResult {
    .init(
      succeeded: false,
      message: "The UI changed before this action could run. A fresh snapshot is required.")
  }

  private func perform(_ action: String, on element: AXUIElement, success: String)
    -> ExecutionResult
  {
    axResult(AXUIElementPerformAction(element, action as CFString), success: success)
  }

  private func axResult(_ result: AXError, success: String) -> ExecutionResult {
    result == .success
      ? .init(succeeded: true, message: success)
      : .init(
        succeeded: false, message: "Accessibility action failed with code \(result.rawValue).")
  }

  private func elementAttribute(_ element: AXUIElement, _ name: String) -> AXUIElement? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success,
      let value, CFGetTypeID(value) == AXUIElementGetTypeID()
    else { return nil }
    return unsafeDowncast(value, to: AXUIElement.self)
  }

  private func boolAttribute(_ element: AXUIElement, _ name: String) -> Bool? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else {
      return nil
    }
    return (value as? NSNumber)?.boolValue
  }

  private func typeUnicode(_ text: String) -> ExecutionResult {
    let units = Array(text.utf16)
    guard !units.isEmpty else { return .init(succeeded: true, message: "Nothing to type.") }
    for chunkStart in stride(from: 0, to: units.count, by: 20) {
      let chunk = Array(units[chunkStart..<min(chunkStart + 20, units.count)])
      guard let event = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true) else {
        return .init(succeeded: false, message: "Could not create text input event.")
      }
      event.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: chunk)
      event.post(tap: .cghidEventTap)
    }
    return .init(succeeded: true, message: "Typed \(text.count) characters.")
  }

  private func postChord(keyCode: CGKeyCode, modifiers: CGEventFlags = []) -> Bool {
    guard let down = CGEvent(keyboardEventSource: nil, virtualKey: keyCode, keyDown: true),
      let up = CGEvent(keyboardEventSource: nil, virtualKey: keyCode, keyDown: false) else { return false }
    down.flags = modifiers
    up.flags = modifiers
    down.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
    return true
  }

  private func openFinderFolder(_ path: String) async -> ExecutionResult {
    var isDirectory: ObjCBool = false
    guard FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory), isDirectory.boolValue else {
      return .init(succeeded: false, message: "Finder folder is unavailable: \(path)")
    }
    guard NSWorkspace.shared.open(URL(fileURLWithPath: path, isDirectory: true)) else {
      return .init(succeeded: false, message: "Finder could not open \(path).")
    }
    for _ in 0..<60 {
      if NSWorkspace.shared.frontmostApplication?.bundleIdentifier == "com.apple.finder",
        finderFocusedFolderMatches(path) {
        return .init(succeeded: true, message: "Finder opened \(path).")
      }
      do { try await Task.sleep(for: .milliseconds(50)) }
      catch { return .init(succeeded: false, message: "Run stopped.") }
    }
    return .init(succeeded: false, message: "Finder did not show the requested folder.")
  }

  private func finderFocusedFolderMatches(_ path: String) -> Bool {
    guard let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier else { return false }
    let app = AXUIElementCreateApplication(pid)
    guard let window = elementAttribute(app, kAXFocusedWindowAttribute) else { return false }
    var value: CFTypeRef?
    if AXUIElementCopyAttributeValue(window, kAXURLAttribute as CFString, &value) == .success,
      let url = value as? URL, url.path == path { return true }
    value = nil
    return AXUIElementCopyAttributeValue(window, kAXTitleAttribute as CFString, &value) == .success
      && (value as? String) == URL(fileURLWithPath: path).lastPathComponent
  }

  private func waitForFocusedTextField() async -> Bool {
    guard let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier else { return false }
    let app = AXUIElementCreateApplication(pid)
    for _ in 0..<40 {
      if let focused = elementAttribute(app, kAXFocusedUIElementAttribute) {
        var value: CFTypeRef?
        if AXUIElementCopyAttributeValue(focused, kAXRoleAttribute as CFString, &value) == .success,
          let role = value as? String,
          role == (kAXTextFieldRole as String) || role == (kAXTextAreaRole as String) { return true }
      }
      do { try await Task.sleep(for: .milliseconds(25)) }
      catch { return false }
    }
    return false
  }

  /// Checks typed text before Return and a distinct Terminal update after Return.
  private func focusedTerminalText() -> String? {
    guard let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier else { return nil }
    let app = AXUIElementCreateApplication(pid)
    guard let focused = elementAttribute(app, kAXFocusedUIElementAttribute) else { return nil }
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(focused, kAXValueAttribute as CFString, &value) == .success else { return nil }
    return value as? String
  }

  private func waitForTerminalText(_ command: String) async -> String? {
    for _ in 0..<40 {
      if let value = focusedTerminalText(), value.contains(command) { return value }
      do { try await Task.sleep(for: .milliseconds(25)) }
      catch { return nil }
    }
    return nil
  }

  private func waitForTerminalChange(after entered: String) async -> Bool {
    for _ in 0..<80 {
      if let value = focusedTerminalText(), value != entered { return true }
      do { try await Task.sleep(for: .milliseconds(25)) }
      catch { return false }
    }
    return false
  }

  private func scroll(lines: Int32, message: String) -> ExecutionResult {
    guard
      let event = CGEvent(
        scrollWheelEvent2Source: nil,
        units: .line,
        wheelCount: 1,
        wheel1: lines,
        wheel2: 0,
        wheel3: 0
      )
    else {
      return .init(succeeded: false, message: "Could not create scroll event.")
    }
    event.post(tap: .cghidEventTap)
    return .init(succeeded: true, message: message)
  }

  private func keyCode(for key: KeyPress) -> CGKeyCode? {
    switch key {
    case .returnKey: 36
    case .escape: 53
    case .tab: 48
    case .space: 49
    case .leftArrow: 123
    case .rightArrow: 124
    case .downArrow: 125
    case .upArrow: 126
    }
  }
}
