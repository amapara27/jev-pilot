// Defines the finite, typed set of actions the app may execute.
import Foundation

/// Represents one concrete, locally generated desktop action.
public enum AutomationAction: Codable, Equatable, Hashable, Sendable {
  case openApp(bundleIdentifier: String, name: String)
  case focusApp(bundleIdentifier: String, name: String)
  case closeWindow(windowID: String, title: String?)
  case minimizeWindow(windowID: String, title: String?)
  case restoreWindow(windowID: String, title: String?)
  case enterFullScreen(windowID: String, title: String?)
  case exitFullScreen(windowID: String, title: String?)
  case clickElement(elementID: String, label: String?)
  case focusElement(elementID: String, label: String?)
  case typeText(elementID: String, text: String)
  case activateMenu(elementID: String, label: String)
  case selectTab(elementID: String, label: String)
  case nextTab
  case previousTab
  case navigateBack
  case navigateForward
  case searchInApp(query: String)
  case finderOpenFolder(path: String)
  case finderSelectItem(elementID: String, url: String)
  case finderOpenItem(elementID: String, url: String)
  case finderRenameItem(elementID: String, url: String, newName: String)
  case finderCopyItem(elementID: String, url: String, destination: String)
  case finderMoveItem(elementID: String, url: String, destination: String)
  case terminalType(command: String)
  case terminalRun(command: String)
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
    case .minimizeWindow: .minimizeWindow
    case .restoreWindow: .restoreWindow
    case .enterFullScreen: .enterFullScreen
    case .exitFullScreen: .exitFullScreen
    case .clickElement: .clickElement
    case .focusElement: .focusElement
    case .typeText: .typeText
    case .activateMenu: .activateMenu
    case .selectTab: .selectTab
    case .nextTab: .nextTab
    case .previousTab: .previousTab
    case .navigateBack: .navigateBack
    case .navigateForward: .navigateForward
    case .searchInApp: .searchInApp
    case .finderOpenFolder: .finderOpenFolder
    case .finderSelectItem: .finderSelectItem
    case .finderOpenItem: .finderOpenItem
    case .finderRenameItem: .finderRenameItem
    case .finderCopyItem: .finderCopyItem
    case .finderMoveItem: .finderMoveItem
    case .terminalType: .terminalType
    case .terminalRun: .terminalRun
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
    case .minimizeWindow(_, let title): "Minimize \(title ?? "window")"
    case .restoreWindow(_, let title): "Restore \(title ?? "window")"
    case .enterFullScreen(_, let title): "Enter fullscreen · \(title ?? "window")"
    case .exitFullScreen(_, let title): "Exit fullscreen · \(title ?? "window")"
    case .clickElement(_, let label): "Click \(label ?? "element")"
    case .focusElement(_, let label): "Focus \(label ?? "element")"
    case .typeText(_, let text): "Type \(text.debugDescription)"
    case .activateMenu(_, let label): "Choose menu \(label)"
    case .selectTab(_, let label): "Select tab \(label)"
    case .nextTab: "Next tab"
    case .previousTab: "Previous tab"
    case .navigateBack: "Navigate back"
    case .navigateForward: "Navigate forward"
    case .searchInApp(let query): "Search for \(query.debugDescription)"
    case .finderOpenFolder(let path): "Open folder \(path)"
    case .finderSelectItem(_, let url): "Select \(URL(fileURLWithPath: url).lastPathComponent)"
    case .finderOpenItem(_, let url): "Open \(URL(fileURLWithPath: url).lastPathComponent)"
    case .finderRenameItem(_, let url, let name): "Rename \(URL(fileURLWithPath: url).lastPathComponent) to \(name)"
    case .finderCopyItem(_, let url, let destination): "Copy \(URL(fileURLWithPath: url).lastPathComponent) to \(destination)"
    case .finderMoveItem(_, let url, let destination): "Move \(URL(fileURLWithPath: url).lastPathComponent) to \(destination)"
    case .terminalType(let command): "Type in Terminal: \(command)"
    case .terminalRun(let command): "Run in Terminal: \(command)"
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
  case minimizeWindow = "MINIMIZE_WINDOW"
  case restoreWindow = "RESTORE_WINDOW"
  case enterFullScreen = "ENTER_FULLSCREEN"
  case exitFullScreen = "EXIT_FULLSCREEN"
  case clickElement = "CLICK_ELEMENT"
  case focusElement = "FOCUS_ELEMENT"
  case typeText = "TYPE_TEXT"
  case activateMenu = "ACTIVATE_MENU"
  case selectTab = "SELECT_TAB"
  case nextTab = "NEXT_TAB"
  case previousTab = "PREVIOUS_TAB"
  case navigateBack = "NAVIGATE_BACK"
  case navigateForward = "NAVIGATE_FORWARD"
  case searchInApp = "SEARCH_IN_APP"
  case finderOpenFolder = "FINDER_OPEN_FOLDER"
  case finderSelectItem = "FINDER_SELECT_ITEM"
  case finderOpenItem = "FINDER_OPEN_ITEM"
  case finderRenameItem = "FINDER_RENAME_ITEM"
  case finderCopyItem = "FINDER_COPY_ITEM"
  case finderMoveItem = "FINDER_MOVE_ITEM"
  case terminalType = "TERMINAL_TYPE"
  case terminalRun = "TERMINAL_RUN"
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
