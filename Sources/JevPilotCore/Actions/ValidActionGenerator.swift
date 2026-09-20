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

  private let supportedApplications: [SupportedApplication]

  public init(supportedApplications: [SupportedApplication] = Self.defaultApplications) {
    self.supportedApplications = supportedApplications
  }

  /// Returns at most 255 current-state candidates, always including STOP.
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
    for application in supportedApplications {
      guard
        let installedBundleID = application.bundleIdentifiers.first(where: {
          NSWorkspace.shared.urlForApplication(withBundleIdentifier: $0) != nil
        }), !runningBundleIDs.contains(installedBundleID)
      else { continue }
      actions.append(
        (
          .openApp(bundleIdentifier: installedBundleID, name: application.name),
          "Launch \(application.name)."
        ))
    }

    if let focusedWindowID = state.focusedWindowID,
      let focusedWindow = state.windows.first(where: { $0.id == focusedWindowID })
    {
      actions.append(
        (
          .closeWindow(windowID: focusedWindowID, title: focusedWindow.title),
          "Close the currently focused window titled \(focusedWindow.title ?? "untitled")."
        ))
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

    let boundedActions = Array(actions.prefix(254)) + [stopAction]
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
    return nil
  }
}
