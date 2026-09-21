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
      do {
        _ = try await NSWorkspace.shared.openApplication(at: url, configuration: .init())
        return .init(succeeded: true, message: "Opened \(name).")
      } catch {
        return .init(
          succeeded: false, message: "Could not open \(name): \(error.localizedDescription)")
      }

    case .focusApp(let bundleIdentifier, let name):
      guard
        let application = NSRunningApplication.runningApplications(
          withBundleIdentifier: bundleIdentifier
        ).first
      else {
        return .init(succeeded: false, message: "\(name) is no longer running.")
      }
      let succeeded = application.activate(options: [.activateAllWindows])
      return .init(
        succeeded: succeeded, message: succeeded ? "Focused \(name)." : "Could not focus \(name).")

    case .closeWindow(let windowID, let title):
      guard let window = perception.element(for: windowID),
        let closeButton = elementAttribute(window, kAXCloseButtonAttribute)
      else {
        return .init(
          succeeded: false, message: "The focused window no longer has an accessible close button.")
      }
      return perform(kAXPressAction, on: closeButton, success: "Closed \(title ?? "window").")

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
