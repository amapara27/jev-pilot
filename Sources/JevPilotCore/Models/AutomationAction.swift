// Defines the finite, typed set of actions the app may execute.
import Foundation

/// Represents one concrete, locally generated desktop action.
public enum AutomationAction: Codable, Equatable, Hashable, Sendable {
  case openApp(bundleIdentifier: String, name: String)
  case focusApp(bundleIdentifier: String, name: String)
  case closeWindow(windowID: String, title: String?)
  case clickElement(elementID: String, label: String?)
  case focusElement(elementID: String, label: String?)
  case typeText(elementID: String, text: String)
  case pressKey(KeyPress)
  case scrollUp
  case scrollDown
  case stop(reason: String)

  /// Returns the stable category used in logs and policy decisions.
  public var kind: ActionKind {
    switch self {
    case .openApp: .openApp
    case .focusApp: .focusApp
    case .closeWindow: .closeWindow
    case .clickElement: .clickElement
    case .focusElement: .focusElement
    case .typeText: .typeText
    case .pressKey: .pressKey
    case .scrollUp: .scrollUp
    case .scrollDown: .scrollDown
    case .stop: .stop
    }
  }

  /// Produces a human-readable description for the UI and debug log.
  public var summary: String {
    switch self {
    case .openApp(_, let name): "Open \(name)"
    case .focusApp(_, let name): "Focus \(name)"
    case .closeWindow(_, let title): "Close window \(title ?? "untitled")"
    case .clickElement(_, let label): "Click \(label ?? "element")"
    case .focusElement(_, let label): "Focus \(label ?? "element")"
    case .typeText(_, let text): "Type \(text.debugDescription)"
    case .pressKey(let key): "Press \(key.rawValue)"
    case .scrollUp: "Scroll up"
    case .scrollDown: "Scroll down"
    case .stop(let reason): "Stop: \(reason)"
    }
  }
}

/// Names the action categories exposed to the decision system.
public enum ActionKind: String, Codable, CaseIterable, Sendable {
  case openApp = "OPEN_APP"
  case focusApp = "FOCUS_APP"
  case closeWindow = "CLOSE_WINDOW"
  case clickElement = "CLICK_ELEMENT"
  case focusElement = "FOCUS_ELEMENT"
  case typeText = "TYPE_TEXT"
  case pressKey = "PRESS_KEY"
  case scrollUp = "SCROLL_UP"
  case scrollDown = "SCROLL_DOWN"
  case stop = "STOP"
}

/// Lists the keyboard inputs that can be generated safely.
public enum KeyPress: String, Codable, CaseIterable, Sendable {
  case returnKey = "return"
  case escape
  case tab
  case space
  case upArrow = "up_arrow"
  case downArrow = "down_arrow"
  case leftArrow = "left_arrow"
  case rightArrow = "right_arrow"
}

/// Connects an opaque model-facing ID to one local action.
public struct ActionCandidate: Codable, Equatable, Sendable, Identifiable {
  public let id: String
  public let action: AutomationAction
  public let criterion: String

  public init(id: String, action: AutomationAction, criterion: String) {
    self.id = id
    self.action = action
    self.criterion = criterion
  }
}
