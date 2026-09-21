// Declares the boundary for reading desktop state.
import Foundation

/// Supplies permission checks and fresh desktop snapshots to the controller.
@MainActor
public protocol DesktopPerceiving: AnyObject {
  func requestAccessibilityPermission(prompt: Bool) -> Bool
  func snapshot(recentActions: [ActionRecord]) throws -> DesktopState
}

/// Describes failures that prevent a desktop snapshot.
public enum PerceptionError: LocalizedError {
  case accessibilityPermissionRequired
  case noFrontmostApplication
  case controlCenterIsFrontmost

  public var errorDescription: String? {
    switch self {
    case .accessibilityPermissionRequired:
      "Accessibility permission is required. Enable Jev Pilot in System Settings → Privacy & Security → Accessibility."
    case .noFrontmostApplication:
      "No frontmost application could be identified."
    case .controlCenterIsFrontmost:
      "Jev Pilot is in front. Switch to the app you want to control and start a new command."
    }
  }
}
