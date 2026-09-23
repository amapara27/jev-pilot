// Reads the frontmost app's Accessibility tree into a bounded desktop snapshot.
import AppKit
import ApplicationServices
import Foundation

/// Maintains snapshot-local native elements while exporting safe serializable state.
@MainActor
public final class AccessibilityPerception: DesktopPerceiving {
  private var elementRegistry: [String: AXUIElement] = [:]
  public private(set) var observedProcessIdentifier: Int32?
  private let maximumElements: Int
  private let maximumDepth: Int

  public init(maximumElements: Int = 120, maximumDepth: Int = 8) {
    self.maximumElements = maximumElements
    self.maximumDepth = maximumDepth
  }

  /// Checks Accessibility trust and optionally opens the system permission prompt.
  public func requestAccessibilityPermission(prompt: Bool) -> Bool {
    guard prompt else { return AXIsProcessTrusted() }
    let options = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
    return AXIsProcessTrustedWithOptions(options)
  }

  /// Captures the active app, windows, controls, and recent action history.
  public func snapshot(recentActions: [ActionRecord]) throws -> DesktopState {
    guard AXIsProcessTrusted() else {
      throw PerceptionError.accessibilityPermissionRequired
    }
    guard let frontmost = NSWorkspace.shared.frontmostApplication else {
      throw PerceptionError.noFrontmostApplication
    }
    observedProcessIdentifier = frontmost.processIdentifier
    elementRegistry.removeAll(keepingCapacity: true)
    let runningApplications = NSWorkspace.shared.runningApplications
      .filter { $0.activationPolicy == .regular && $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }
      .compactMap { application -> ApplicationState? in
        guard let name = application.localizedName else { return nil }
        return ApplicationState(name: name, bundleIdentifier: application.bundleIdentifier, processIdentifier: application.processIdentifier)
      }
      .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    // A voice command may launch an app even when the control center is the only frontmost app.
    if frontmost.processIdentifier == ProcessInfo.processInfo.processIdentifier {
      return DesktopState(runningApplications: runningApplications, isAccessibilityTrusted: true, recentActions: Array(recentActions.suffix(8)))
    }
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
            isFocused: focusedWindow.map { CFEqual($0, window) } ?? false,
            isMinimized: boolAttribute(window, kAXMinimizedAttribute) ?? false,
            isFullScreen: boolAttribute(window, "AXFullScreen"),
            url: filePath(for: window)
          ))
      }
    }

    var elements: [UIElementState] = []
    walk(
      element: focusedWindow ?? appElement,
      path: "root",
      depth: 0,
      focusedElement: focusedElement,
      limit: maximumElements,
      output: &elements
    )
    if let menuBar = elementAttribute(appElement, kAXMenuBarAttribute) {
      // Reserve a small independent budget so large windows cannot hide the menu bar.
      var menus: [UIElementState] = []
      walk(element: menuBar, path: "menu", depth: 0, focusedElement: focusedElement,
        limit: 40, output: &menus)
      elements.append(contentsOf: menus)
    }

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

  /// Resolves a snapshot-local ID for the executor, if it is still current.
  public func element(for id: String) -> AXUIElement? {
    elementRegistry[id]
  }

  private func walk(
    element: AXUIElement,
    path: String,
    depth: Int,
    focusedElement: AXUIElement?,
    limit: Int,
    output: inout [UIElementState]
  ) {
    guard depth <= maximumDepth, output.count < limit else { return }

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
      kAXMenuBarRole as String,
      kAXMenuBarItemRole as String,
      kAXMenuRole as String,
      kAXPopUpButtonRole as String,
      kAXComboBoxRole as String,
      kAXTabGroupRole as String,
      kAXRowRole as String,
      kAXCellRole as String,
      "AXLink",
      kAXSliderRole as String,
      kAXScrollAreaRole as String,
      kAXStaticTextRole as String,
      "AXHeading",
    ]

    let itemURL = filePath(for: element)
    if interactiveRoles.contains(role) || !actions.isEmpty || itemURL != nil {
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
          depth: depth,
          url: itemURL,
          isSelected: boolAttribute(element, kAXSelectedAttribute) ?? false
        ))
    }

    guard let children = attribute(element, kAXChildrenAttribute) as? [AXUIElement] else { return }
    for (index, child) in children.enumerated() where output.count < limit {
      walk(
        element: child,
        path: "\(path).\(index)",
        depth: depth + 1,
        focusedElement: focusedElement,
        limit: limit,
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

  private func filePath(for element: AXUIElement) -> String? {
    guard let value = attribute(element, kAXURLAttribute) else { return nil }
    if let url = value as? URL, url.isFileURL { return url.path }
    if let string = value as? String {
      if string.hasPrefix("/") { return string }
      if let url = URL(string: string), url.isFileURL { return url.path }
    }
    return nil
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
