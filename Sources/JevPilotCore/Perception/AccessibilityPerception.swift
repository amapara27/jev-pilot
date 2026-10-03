// Reads the frontmost app's Accessibility tree into a bounded desktop snapshot.
import AppKit
import ApplicationServices
import Foundation

/// Maintains snapshot-local native elements while exporting safe serializable state.
@MainActor
public final class AccessibilityPerception: DesktopPerceiving {
  private var elementRegistry: [String: AXUIElement] = [:]
  private var previousRegistry: [String: AXUIElement] = [:]
  private var nextElementID = 0
  public private(set) var observedProcessIdentifier: Int32?
  private var recentActions: [ActionRecord] = []
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
    previousRegistry = observedProcessIdentifier == frontmost.processIdentifier ? elementRegistry : [:]
    observedProcessIdentifier = frontmost.processIdentifier
    self.recentActions = recentActions
    elementRegistry.removeAll(keepingCapacity: true)
    let runningApplications = NSWorkspace.shared.runningApplications
      .filter { $0.activationPolicy == .regular && $0.processIdentifier != ProcessInfo.processInfo.processIdentifier
        && (Bundle.main.bundleIdentifier == nil || $0.bundleIdentifier != Bundle.main.bundleIdentifier) }
      .compactMap { application -> ApplicationState? in
        guard let name = application.localizedName else { return nil }
        return ApplicationState(name: name, bundleIdentifier: application.bundleIdentifier, processIdentifier: application.processIdentifier)
      }
      .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    // A voice command may launch an app even when the control center is the only frontmost app.
    if frontmost.processIdentifier == ProcessInfo.processInfo.processIdentifier
      || (Bundle.main.bundleIdentifier != nil && frontmost.bundleIdentifier == Bundle.main.bundleIdentifier) {
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
    // Deep editors can sit outside the traversal budget. Always retain the actual input target.
    if let focusedElement, !elements.contains(where: \.isFocused) {
      let id = idForRegisteredElement(focusedElement) ?? register(focusedElement, path: "focused")
      elements.append(elementState(focusedElement, id: id, depth: 0, isFocused: true))
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

  /// Reads an input's full local value without replacing the snapshot's AX registry.
  func textState(for element: AXUIElement) -> UIElementState {
    elementState(element, id: idForRegisteredElement(element) ?? "input", depth: 0, isFocused: true)
  }

  var recentlyEnteredTerminalCommand: String? {
    guard let last = recentActions.last(where: {
      guard $0.succeeded else { return false }
      if case .terminalType = $0.action { return true }
      return false
    }),
      case .terminalType(let command) = last.action else { return nil }
    return command
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
      output.append(elementState(element, id: id, depth: depth,
        isFocused: focusedElement.map { CFEqual($0, element) } ?? false))
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
    if let id = idForRegisteredElement(element) { return id }
    let id: String
    if let existing = previousRegistry.first(where: { CFEqual($0.value, element) })?.key {
      id = existing
    } else {
      nextElementID += 1
      id = "ax:\(observedProcessIdentifier ?? 0):\(nextElementID)"
    }
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

  /// Retains bounded local text for exact verification; provider requests truncate it separately.
  private func elementState(_ element: AXUIElement, id: String, depth: Int, isFocused: Bool) -> UIElementState {
    let role = stringAttribute(element, kAXRoleAttribute) ?? "AXUnknown"
    let (value, truncated) = safeValue(for: element, role: role)
    return UIElementState(id: id, role: role,
      subrole: stringAttribute(element, kAXSubroleAttribute), label: preferredLabel(for: element),
      value: value, isEnabled: boolAttribute(element, kAXEnabledAttribute) ?? true,
      isFocused: isFocused, supportedActions: actionNames(element), depth: depth,
      url: filePath(for: element), isSelected: boolAttribute(element, kAXSelectedAttribute) ?? false,
      textSelection: selectedTextRange(for: element), valueIsTruncated: truncated)
  }

  private func safeValue(for element: AXUIElement, role: String) -> (String?, Bool) {
    let subrole = stringAttribute(element, kAXSubroleAttribute)?.lowercased() ?? ""
    if subrole.contains("secure") || subrole.contains("password") { return ("<redacted>", false) }
    guard let value = attribute(element, kAXValueAttribute) else { return (nil, false) }
    if let string = value as? String {
      let limit = ["AXTextField", "AXTextArea", "AXComboBox"].contains(role) ? TextInput.maximumValueLength : 240
      return (String(string.prefix(limit)), string.count > limit)
    }
    if let number = value as? NSNumber { return (number.stringValue, false) }
    return (nil, false)
  }

  /// Accessibility represents the caret and selection as a UTF-16 CFRange.
  private func selectedTextRange(for element: AXUIElement) -> TextSelection? {
    guard let value = attribute(element, kAXSelectedTextRangeAttribute),
      CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
    let axValue = unsafeDowncast(value, to: AXValue.self)
    var range = CFRange()
    guard AXValueGetValue(axValue, .cfRange, &range), range.location >= 0, range.length >= 0 else { return nil }
    return TextSelection(location: range.location, length: range.length)
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
