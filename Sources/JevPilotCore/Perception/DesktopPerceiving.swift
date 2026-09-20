import Foundation

@MainActor
public protocol DesktopPerceiving: AnyObject {
  func requestAccessibilityPermission(prompt: Bool) -> Bool
  func snapshot(recentActions: [ActionRecord]) throws -> DesktopState
}

public enum PerceptionError: LocalizedError {
  case accessibilityPermissionRequired
  case noFrontmostApplication

  public var errorDescription: String? {
    switch self {
    case .accessibilityPermissionRequired:
      "Accessibility permission is required. Enable Jev Pilot in System Settings → Privacy & Security → Accessibility."
    case .noFrontmostApplication:
      "No frontmost application could be identified."
    }
  }
}
