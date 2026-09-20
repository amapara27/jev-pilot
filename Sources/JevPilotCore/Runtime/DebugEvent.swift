// Defines debug timeline entries and paused confirmation state.
import Foundation

/// Records one observable event during an automation run.
public struct DebugEvent: Identifiable, Sendable {
  /// Categorizes timeline entries for display and diagnosis.
  public enum Kind: String, Sendable {
    case observation
    case candidates
    case decision
    case safety
    case execution
    case error
  }

  public let id = UUID()
  public let timestamp = Date()
  public let kind: Kind
  public let title: String
  public let detail: String

  public init(kind: Kind, title: String, detail: String) {
    self.kind = kind
    self.title = title
    self.detail = detail
  }
}

/// Holds an approved-but-not-yet-executed action while awaiting the user.
public struct PendingConfirmation: Sendable {
  public let goal: String
  public let nextStep: Int
  public let decision: ActionDecision
  public let assessment: SafetyAssessment
}
