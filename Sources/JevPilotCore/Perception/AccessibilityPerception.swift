import AppKit
import ApplicationServices
import Foundation

@MainActor
public final class AccessibilityPerception: DesktopPerceiving {
  private var elementRegistry: [String: AXUIElement] = [:]
  private let maximumElements: Int
  private let maximumDepth: Int

  public init(maximumElements: Int = 120, maximumDepth: Int = 8) {
    self.maximumElements = maximumElements
    self.maximumDepth = maximumDepth
  }

  public func requestAccessibilityPermission(prompt: Bool) -> Bool {
    guard prompt else { return AXIsProcessTrusted() }
    let options = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
    return AXIsProcessTrustedWithOptions(options)
  }

  public func snapshot(recentActions: [ActionRecord]) throws -> DesktopState {
    guard AXIsProcessTrusted() else {
      throw PerceptionError.accessibilityPermissionRequired
    }
    guard let frontmost = NSWorkspace.shared.frontmostApplication else {
      throw PerceptionError.noFrontmostApplication
    }

    elementRegistry.removeAll(keepingCapacity: true)
    let appElement = AXUIElementCreateApplication(frontmost.processIdentifier)
    let focusedWindow = elementAttribute(appElement, kAXFocusedWindowAttribute)
    let focusedElement = elementAttribute(appElement, kAXFocusedUIElementAttribute)

    var windows: [WindowState] = []
    if let axWindows = attribute(appElement, kAXWindowsAttribute) as? [AXUIElement] {
      for (index, window) in axWindows.enumerated() {
        let id = register(window, path: "window.\(index)")
        windows.append(
          WindowState(
            id: id,
            title: stringAttribute(window, kAXTitleAttribute),
            role: stringAttribute(window, kAXRoleAttribute) ?? "AXWindow",
            isFocused: focusedWindow.map { CFEqual($0, window) } ?? false
          ))
      }
    }

    var elements: [UIElementState] = []
    walk(
      element: focusedWindow ?? appElement,
      path: "root",
      depth: 0,
      focusedElement: focusedElement,
      output: &elements
    )

    let runningApplications = NSWorkspace.shared.runningApplications
      .filter { $0.activationPolicy == .regular }
      .compactMap { application -> ApplicationState? in
        guard let name = application.localizedName else { return nil }
        return ApplicationState(
          name: name,
          bundleIdentifier: application.bundleIdentifier,
          processIdentifier: application.processIdentifier
        )
      }
      .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }

    return DesktopState(
      activeApplication: ApplicationState(
        name: frontmost.localizedName ?? "Unknown",
        bundleIdentifier: frontmost.bundleIdentifier,
        processIdentifier: frontmost.processIdentifier
      ),
      runningApplications: runningApplications,
      windows: windows,
      focusedWindowID: windows.first(where: \.isFocused)?.id,
      focusedElementID: focusedElement.flatMap(idForRegisteredElement),
      elements: elements,
      isAccessibilityTrusted: true,
      recentActions: Array(recentActions.suffix(8))
    )
  }

  public func element(for id: String) -> AXUIElement? {
    elementRegistry[id]
  }

  private func walk(
    element: AXUIElement,
    path: String,
    depth: Int,
    focusedElement: AXUIElement?,
    output: inout [UIElementState]
  ) {
    guard depth <= maximumDepth, output.count < maximumElements else { return }

    let role = stringAttribute(element, kAXRoleAttribute) ?? "AXUnknown"
    let actions = actionNames(element)
    let id = register(element, path: path)
    let interactiveRoles: Set<String> = [
      kAXButtonRole as String,
      kAXCheckBoxRole as String,
      kAXRadioButtonRole as String,
      kAXTextFieldRole as String,
      kAXTextAreaRole as String,
      kAXMenuItemRole as String,
      kAXPopUpButtonRole as String,
      kAXComboBoxRole as String,
      kAXTabGroupRole as String,
      "AXLink",
      kAXSliderRole as String,
      kAXScrollAreaRole as String,
      kAXStaticTextRole as String,
      "AXHeading",
    ]

    if interactiveRoles.contains(role) || !actions.isEmpty {
      output.append(
        UIElementState(
          id: id,
          role: role,
          subrole: stringAttribute(element, kAXSubroleAttribute),
          label: preferredLabel(for: element),
          value: safeValue(for: element, role: role),
          isEnabled: boolAttribute(element, kAXEnabledAttribute) ?? true,
          isFocused: focusedElement.map { CFEqual($0, element) } ?? false,
          supportedActions: actions,
          depth: depth
        ))
    }

    guard let children = attribute(element, kAXChildrenAttribute) as? [AXUIElement] else { return }
    for (index, child) in children.enumerated() where output.count < maximumElements {
      walk(
        element: child,
        path: "\(path).\(index)",
        depth: depth + 1,
        focusedElement: focusedElement,
        output: &output
      )
    }
  }

  private func register(_ element: AXUIElement, path: String) -> String {
    let id = "ax:\(path)"
    elementRegistry[id] = element
    return id
  }

  private func idForRegisteredElement(_ element: AXUIElement) -> String? {
    elementRegistry.first(where: { CFEqual($0.value, element) })?.key
  }

  private func preferredLabel(for element: AXUIElement) -> String? {
    stringAttribute(element, kAXTitleAttribute)
      ?? stringAttribute(element, kAXDescriptionAttribute)
      ?? stringAttribute(element, kAXHelpAttribute)
      ?? stringAttribute(element, kAXIdentifierAttribute)
  }

  private func safeValue(for element: AXUIElement, role: String) -> String? {
    let subrole = stringAttribute(element, kAXSubroleAttribute)?.lowercased() ?? ""
    if subrole.contains("secure") || subrole.contains("password") { return "<redacted>" }
    guard let value = attribute(element, kAXValueAttribute) else { return nil }
    if let string = value as? String { return String(string.prefix(240)) }
    if let number = value as? NSNumber { return number.stringValue }
    return role == (kAXTextFieldRole as String) ? "<unavailable>" : nil
  }

  private func actionNames(_ element: AXUIElement) -> [String] {
    var names: CFArray?
    guard AXUIElementCopyActionNames(element, &names) == .success else { return [] }
    return (names as? [String]) ?? []
  }

  private func elementAttribute(_ element: AXUIElement, _ name: String) -> AXUIElement? {
    guard let value = attribute(element, name), CFGetTypeID(value) == AXUIElementGetTypeID() else {
      return nil
    }
    return unsafeDowncast(value, to: AXUIElement.self)
  }

  private func stringAttribute(_ element: AXUIElement, _ name: String) -> String? {
    attribute(element, name) as? String
  }

  private func boolAttribute(_ element: AXUIElement, _ name: String) -> Bool? {
    (attribute(element, name) as? NSNumber)?.boolValue
  }

  private func attribute(_ element: AXUIElement, _ name: String) -> AnyObject? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else {
      return nil
    }
    return value
  }
}
