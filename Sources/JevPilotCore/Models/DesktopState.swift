// Defines the serializable snapshot of the desktop used to choose actions.
import Foundation

/// Captures the relevant visible desktop state for one automation step.
public struct DesktopState: Codable, Equatable, Sendable {
  public var capturedAt: Date
  public var activeApplication: ApplicationState?
  public var runningApplications: [ApplicationState]
  public var windows: [WindowState]
  public var focusedWindowID: String?
  public var focusedElementID: String?
  public var elements: [UIElementState]
  public var isAccessibilityTrusted: Bool
  public var recentActions: [ActionRecord]

  public init(
    capturedAt: Date = .now,
    activeApplication: ApplicationState? = nil,
    runningApplications: [ApplicationState] = [],
    windows: [WindowState] = [],
    focusedWindowID: String? = nil,
    focusedElementID: String? = nil,
    elements: [UIElementState] = [],
    isAccessibilityTrusted: Bool = false,
    recentActions: [ActionRecord] = []
  ) {
    self.capturedAt = capturedAt
    self.activeApplication = activeApplication
    self.runningApplications = runningApplications
    self.windows = windows
    self.focusedWindowID = focusedWindowID
    self.focusedElementID = focusedElementID
    self.elements = elements
    self.isAccessibilityTrusted = isAccessibilityTrusted
    self.recentActions = recentActions
  }
}

/// Describes a running macOS application.
public struct ApplicationState: Codable, Equatable, Hashable, Sendable {
  public let name: String
  public let bundleIdentifier: String?
  public let processIdentifier: Int32?

  public init(name: String, bundleIdentifier: String? = nil, processIdentifier: Int32? = nil) {
    self.name = name
    self.bundleIdentifier = bundleIdentifier
    self.processIdentifier = processIdentifier
  }
}

/// Describes an accessible application window.
public struct WindowState: Codable, Equatable, Sendable, Identifiable {
  public let id: String
  public let title: String?
  public let role: String
  public let isFocused: Bool

  public init(id: String, title: String?, role: String, isFocused: Bool) {
    self.id = id
    self.title = title
    self.role = role
    self.isFocused = isFocused
  }
}

/// Describes one visible accessibility element without exposing the native object.
public struct UIElementState: Codable, Equatable, Sendable, Identifiable {
  public let id: String
  public let role: String
  public let subrole: String?
  public let label: String?
  public let value: String?
  public let isEnabled: Bool
  public let isFocused: Bool
  public let supportedActions: [String]
  public let depth: Int

  public init(
    id: String,
    role: String,
    subrole: String? = nil,
    label: String? = nil,
    value: String? = nil,
    isEnabled: Bool = true,
    isFocused: Bool = false,
    supportedActions: [String] = [],
    depth: Int = 0
  ) {
    self.id = id
    self.role = role
    self.subrole = subrole
    self.label = label
    self.value = value
    self.isEnabled = isEnabled
    self.isFocused = isFocused
    self.supportedActions = supportedActions
    self.depth = depth
  }
}

/// Records one attempted action for history and later observations.
public struct ActionRecord: Codable, Equatable, Sendable, Identifiable {
  public let id: UUID
  public let timestamp: Date
  public let action: AutomationAction
  public let succeeded: Bool
  public let message: String

  public init(
    id: UUID = UUID(),
    timestamp: Date = .now,
    action: AutomationAction,
    succeeded: Bool,
    message: String
  ) {
    self.id = id
    self.timestamp = timestamp
    self.action = action
    self.succeeded = succeeded
    self.message = message
  }
}
