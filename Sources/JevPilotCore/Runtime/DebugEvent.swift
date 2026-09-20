import Foundation

public struct DebugEvent: Identifiable, Sendable {
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

public struct PendingConfirmation: Sendable {
  public let goal: String
  public let nextStep: Int
  public let decision: ActionDecision
  public let assessment: SafetyAssessment
}
