// Builds the finite list of actions that are valid for the current snapshot.
import AppKit
import ApplicationServices
import Foundation

/// Derives safe, concrete candidates instead of parsing model-supplied commands.
@MainActor
public struct ValidActionGenerator {
  /// Identifies an application that the generator may offer to launch.
  public struct SupportedApplication: Sendable {
    public let name: String
    public let bundleIdentifiers: [String]

    public init(name: String, bundleIdentifiers: [String]) {
      self.name = name
      self.bundleIdentifiers = bundleIdentifiers
    }
  }

  public static let defaultApplications = [
    SupportedApplication(name: "Finder", bundleIdentifiers: ["com.apple.finder"]),
    SupportedApplication(name: "Terminal", bundleIdentifiers: ["com.apple.Terminal"]),
    SupportedApplication(name: "Visual Studio Code", bundleIdentifiers: ["com.microsoft.VSCode"]),
    SupportedApplication(name: "Safari", bundleIdentifiers: ["com.apple.Safari"]),
    SupportedApplication(name: "Google Chrome", bundleIdentifiers: ["com.google.Chrome"]),
    SupportedApplication(
      name: "System Settings", bundleIdentifiers: ["com.apple.systempreferences"]),
  ]

  private let supportedApplications: [SupportedApplication]?
  private let terminalExecutionEnabled: () -> Bool
  private let applicationURL: (String) -> URL?

  public init(
    supportedApplications: [SupportedApplication]? = nil,
    terminalExecutionEnabled: @escaping () -> Bool = { false },
    applicationURL: @escaping (String) -> URL? = { NSWorkspace.shared.urlForApplication(withBundleIdentifier: $0) }
  ) {
    self.supportedApplications = supportedApplications
    self.terminalExecutionEnabled = terminalExecutionEnabled
    self.applicationURL = applicationURL
  }

  /// Prepares the installed-app index off the UI actor before the first decision.
  public func prepareInstalledApplications() async {
    if supportedApplications == nil { await InstalledApplicationCatalog.prepare() }
  }

  /// Keeps goal-specific choices first and a bounded set of generic AX controls.
  public func candidates(for goal: String, state: DesktopState) -> [ActionCandidate] {
    var actions: [(AutomationAction, String)] = []
    let activeBundleID = state.activeApplication?.bundleIdentifier

    for application in state.runningApplications {
      guard let bundleID = application.bundleIdentifier, bundleID != activeBundleID else {
        continue
      }
      actions.append(
        (
          .focusApp(bundleIdentifier: bundleID, name: application.name),
          "Bring the already-running \(application.name) application to the foreground."
        ))
    }

    let runningBundleIDs = Set(state.runningApplications.compactMap(\.bundleIdentifier))
    let catalog = supportedApplications ?? (Self.defaultApplications + InstalledApplicationCatalog.applications)
    let terminalIntent = Self.payload(after: ["run command ", "run ", "terminal command ", "type command "], in: goal) != nil
    var offeredBundles: Set<String> = []
    for application in catalog where Self.matchesSpokenApplication(application, in: goal)
      || (terminalIntent && application.bundleIdentifiers.contains("com.apple.Terminal")) {
      for installedBundleID in application.bundleIdentifiers {
        guard applicationURL(installedBundleID) != nil,
          !runningBundleIDs.contains(installedBundleID),
          offeredBundles.insert(installedBundleID).inserted else { continue }
        actions.append((.openApp(bundleIdentifier: installedBundleID, name: application.name),
          "Launch \(application.name) (\(installedBundleID))."))
      }
    }

    if let focusedWindowID = state.focusedWindowID,
      let focusedWindow = state.windows.first(where: { $0.id == focusedWindowID })
    {
      actions.append(
        (
          .closeWindow(windowID: focusedWindowID, title: focusedWindow.title),
          "Close the currently focused window titled \(focusedWindow.title ?? "untitled")."
        ))
      actions.append((.minimizeWindow(windowID: focusedWindowID, title: focusedWindow.title), "Minimize the focused window."))
      if focusedWindow.isFullScreen == false {
        actions.append((.enterFullScreen(windowID: focusedWindowID, title: focusedWindow.title), "Enter fullscreen for the focused window."))
      } else if focusedWindow.isFullScreen == true {
        actions.append((.exitFullScreen(windowID: focusedWindowID, title: focusedWindow.title), "Exit fullscreen for the focused window."))
      }
    }
    for window in state.windows where window.isMinimized {
      actions.append((.restoreWindow(windowID: window.id, title: window.title), "Restore the minimized window."))
    }

    for element in state.elements where element.isEnabled {
      let label = element.label ?? element.value ?? element.role
      if element.supportedActions.contains(kAXPressAction as String) {
        actions.append(
          (
            .clickElement(elementID: element.id, label: label),
            "Activate the visible \(element.role) labeled \(label)."
          ))
      }
      if Self.isFocusable(element) && !element.isFocused {
        actions.append(
          (
            .focusElement(elementID: element.id, label: label),
            "Move keyboard focus to the \(element.role) labeled \(label)."
          ))
      }
      if [kAXMenuItemRole as String, kAXMenuBarItemRole as String].contains(element.role),
        element.supportedActions.contains(kAXPressAction as String) {
        actions.append((.activateMenu(elementID: element.id, label: label), "Choose the \(label) menu item."))
      }
      if element.role == (kAXRadioButtonRole as String), element.subrole?.lowercased().contains("tab") == true {
        actions.append((.selectTab(elementID: element.id, label: label), "Select the \(label) tab."))
      }
    }

    if let text = Self.textToType(from: goal),
      let focused = state.elements.first(where: { $0.isFocused && Self.isTextEntry($0) })
    {
      actions.append(
        (
          .typeText(elementID: focused.id, text: text),
          "Type the exact user-provided text into the focused field."
        ))
    }

    if let query = Self.payload(after: ["search for ", "find ", "search "], in: goal) {
      actions.append((.searchInApp(query: query), "Search in the active app for the exact phrase."))
    }
    if ["com.apple.Safari", "com.google.Chrome", "com.microsoft.VSCode", "com.apple.finder"].contains(activeBundleID ?? "") {
      actions.append((.navigateBack, "Navigate back in the active app."))
      actions.append((.navigateForward, "Navigate forward in the active app."))
      actions.append((.nextTab, "Select the next tab in the active app."))
      actions.append((.previousTab, "Select the previous tab in the active app."))
    }

    if activeBundleID == "com.apple.Terminal",
      let command = Self.payload(after: ["run command ", "run ", "terminal command ", "type command "], in: goal)
    {
      actions.append((.terminalType(command: command), "Type the exact command in Terminal without running it."))
      if terminalExecutionEnabled() {
        actions.append((.terminalRun(command: command), "Type and submit the exact Terminal command after approval."))
      }
    }

    if activeBundleID == "com.apple.finder" {
      if let path = Self.folder(in: goal) {
        actions.append((.finderOpenFolder(path: path), "Open the exact folder in Finder."))
      }
      let matching = state.elements.filter { element in
        guard Self.isFinderItem(element), let path = element.url,
          path.hasPrefix("/"), let label = element.label else { return false }
        return goal.localizedCaseInsensitiveContains(label)
      }
      if matching.count == 1, let item = matching.first, let path = item.url {
        actions.append((.finderSelectItem(elementID: item.id, url: path), "Select the uniquely named Finder item."))
        actions.append((.finderOpenItem(elementID: item.id, url: path), "Open the uniquely named Finder item."))
      }
      let selected = state.elements.filter {
        Self.isFinderItem($0) && $0.isSelected && $0.url?.hasPrefix("/") == true
      }
      if selected.count == 1, let item = selected.first, let path = item.url {
        actions.append((.finderOpenItem(elementID: item.id, url: path), "Open the selected Finder item."))
        if let name = Self.renameTarget(in: goal) {
          actions.append((.finderRenameItem(elementID: item.id, url: path, newName: name), "Rename the selected Finder item."))
        }
        if let destination = Self.destination(in: goal, verb: "copy") {
          actions.append((.finderCopyItem(elementID: item.id, url: path, destination: destination), "Copy the selected Finder item."))
        }
        if let destination = Self.destination(in: goal, verb: "move") {
          actions.append((.finderMoveItem(elementID: item.id, url: path, destination: destination), "Move the selected Finder item."))
        }
      }
    }

    if state.elements.contains(where: { $0.role == (kAXScrollAreaRole as String) }) {
      actions.append((.scrollUp, "Scroll the current view upward to reveal earlier content."))
      actions.append((.scrollDown, "Scroll the current view downward to reveal later content."))
    }

    for key in KeyPress.allCases {
      actions.append((.pressKey(key), "Press the \(key.rawValue) key in the active application."))
    }

    let stopAction: (AutomationAction, String) = (
      .stop(reason: "Goal complete or no safe valid action remains"),
      "Stop when the requested goal is already complete, cannot be advanced with the available actions, or needs the user."
    )

    let prioritized = actions.enumerated().sorted { left, right in
      let first = Self.priority(of: left.element.0, goal: goal)
      let second = Self.priority(of: right.element.0, goal: goal)
      return first == second ? left.offset < right.offset : first < second
    }
    let boundedActions = Array(prioritized.prefix(79).map(\.element)) + [stopAction]
    return boundedActions.enumerated().map { index, item in
      ActionCandidate(id: "action_\(index)", action: item.0, criterion: item.1)
    }
  }

  private static func isFocusable(_ element: UIElementState) -> Bool {
    isTextEntry(element)
      || [
        kAXButtonRole as String,
        kAXCheckBoxRole as String,
        kAXRadioButtonRole as String,
        kAXComboBoxRole as String,
        kAXPopUpButtonRole as String,
      ].contains(element.role)
  }

  private static func isFinderItem(_ element: UIElementState) -> Bool {
    [kAXRowRole as String, kAXCellRole as String, kAXImageRole as String].contains(element.role)
  }

  private static func matchesSpokenApplication(_ application: SupportedApplication, in goal: String) -> Bool {
    if goal.localizedCaseInsensitiveContains(application.name) { return true }
    let aliases: [String: [String]] = [
      "com.google.Chrome": ["Chrome"],
      "com.microsoft.VSCode": ["VS Code"],
      "com.apple.systempreferences": ["Settings"],
    ]
    return application.bundleIdentifiers.contains { id in
      (aliases[id] ?? []).contains { goal.localizedCaseInsensitiveContains($0) }
    }
  }

  private static func priority(of action: AutomationAction, goal: String) -> Int {
    switch action {
    case .openApp, .focusApp, .typeText, .searchInApp, .finderOpenFolder,
      .finderSelectItem, .finderOpenItem, .finderRenameItem, .finderCopyItem,
      .finderMoveItem, .terminalType, .terminalRun, .minimizeWindow,
      .restoreWindow, .enterFullScreen, .exitFullScreen, .nextTab,
      .previousTab, .navigateBack, .navigateForward:
      return 0
    case .clickElement(_, let label), .focusElement(_, let label):
      return label.map { goal.localizedCaseInsensitiveContains($0) } == true ? 1 : 2
    case .activateMenu(_, let label), .selectTab(_, let label):
      return goal.localizedCaseInsensitiveContains(label) ? 1 : 2
    default: return 2
    }
  }

  private static func isTextEntry(_ element: UIElementState) -> Bool {
    element.role == (kAXTextFieldRole as String) || element.role == (kAXTextAreaRole as String)
  }

  private static func textToType(from goal: String) -> String? {
    let patterns = [
      #"(?i)\btype\s+[\"“](.+?)[\"”]"#,
      #"(?i)\benter\s+[\"“](.+?)[\"”]"#,
    ]
    for pattern in patterns {
      guard let regex = try? NSRegularExpression(pattern: pattern),
        let match = regex.firstMatch(in: goal, range: NSRange(goal.startIndex..., in: goal)),
        let range = Range(match.range(at: 1), in: goal)
      else { continue }
      return String(goal[range])
    }
    return payload(after: ["type ", "write ", "enter ", "dictate "], in: goal)
  }

  private static func payload(after prefixes: [String], in goal: String) -> String? {
    let lower = goal.lowercased()
    guard let prefix = prefixes.first(where: { lower.hasPrefix($0) }) else { return nil }
    let value = String(goal.dropFirst(prefix.count)).trimmingCharacters(in: .whitespacesAndNewlines)
    return value.isEmpty ? nil : value
  }

  private static func folder(in goal: String) -> String? {
    let lower = goal.lowercased()
    let standard = ["downloads": "Downloads", "documents": "Documents", "desktop": "Desktop", "applications": "Applications"]
    if let name = standard.keys.sorted().first(where: { lower.contains($0) }), let folder = standard[name] {
      return folder == "Applications" ? "/Applications" : NSHomeDirectory() + "/" + folder
    }
    guard let path = payload(after: ["open folder ", "go to folder "], in: goal), path.hasPrefix("/") else { return nil }
    return path
  }

  private static func destination(in goal: String, verb: String) -> String? {
    guard goal.lowercased().hasPrefix(verb + " "),
      let range = goal.range(of: " to ", options: [.backwards, .caseInsensitive]) else { return nil }
    let value = String(goal[range.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
    let standard = ["downloads": "Downloads", "documents": "Documents", "desktop": "Desktop"]
    if let folder = standard[value.lowercased()] { return NSHomeDirectory() + "/" + folder }
    return value.hasPrefix("/") ? value : nil
  }

  private static func renameTarget(in goal: String) -> String? {
    if let direct = payload(after: ["rename to ", "call it "], in: goal) { return direct }
    guard goal.lowercased().hasPrefix("rename "),
      let range = goal.range(of: " to ", options: [.backwards, .caseInsensitive]) else { return nil }
    let value = String(goal[range.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
    return value.isEmpty ? nil : value
  }
}
