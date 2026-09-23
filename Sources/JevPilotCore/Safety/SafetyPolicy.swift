// Applies deterministic local risk rules to proposed automation actions.
import Foundation

/// Converts action semantics, UI metadata, and confidence into a safety decision.
public struct SafetyPolicy: Sendable {
  public var lowRiskConfidenceThreshold: Double
  public var mediumRiskConfidenceThreshold: Double

  public init(
    lowRiskConfidenceThreshold: Double = 0.65, mediumRiskConfidenceThreshold: Double = 0.82
  ) {
    self.lowRiskConfidenceThreshold = lowRiskConfidenceThreshold
    self.mediumRiskConfidenceThreshold = mediumRiskConfidenceThreshold
  }

  /// Returns the allow, confirmation, or denial result for one concrete action.
  public func assess(action: AutomationAction, confidence: Double, state: DesktopState)
    -> SafetyAssessment
  {
    if targetsSecureField(action, state: state) {
      return SafetyAssessment(
        risk: .blocked,
        disposition: .deny,
        reason: "Jev Pilot never types into secure or password fields."
      )
    }

    if containsBlockedIntent(action) {
      return SafetyAssessment(
        risk: .blocked,
        disposition: .deny,
        reason: "The target appears to involve a purchase or destructive action."
      )
    }

    let risk = riskLevel(for: action)
    switch risk {
    case .low:
      return confidence >= lowRiskConfidenceThreshold
        ? SafetyAssessment(
          risk: .low, disposition: .allow,
          reason: "Low-risk action passed the confidence threshold.")
        : SafetyAssessment(
          risk: .low, disposition: .requireConfirmation,
          reason: "Low Jev confidence requires confirmation.")
    case .medium:
      return confidence >= mediumRiskConfidenceThreshold
        ? SafetyAssessment(
          risk: .medium, disposition: .allow,
          reason: "Medium-risk action passed the confidence threshold.")
        : SafetyAssessment(
          risk: .medium, disposition: .requireConfirmation,
          reason: "Medium-risk action needs higher confidence or confirmation.")
    case .high:
      return SafetyAssessment(
        risk: .high, disposition: .requireConfirmation,
        reason: "High-risk actions always require confirmation.")
    case .blocked:
      return SafetyAssessment(
        risk: .blocked, disposition: .deny, reason: "This action is blocked by local policy.")
    }
  }

  /// Blocks credential and payment goals before any provider request is made.
  public func blockedReason(forGoal goal: String) -> String? {
    let normalized = goal.lowercased()
    let sensitiveTerms = ["password", "passcode", "credit card", "security code", "cvv"]
    guard sensitiveTerms.contains(where: normalized.contains) else { return nil }
    return
      "Commands containing credentials or payment data are blocked before any desktop state is sent to Jev."
  }

  /// Assigns the base risk level from the action's semantics.
  public func riskLevel(for action: AutomationAction) -> RiskLevel {
    switch action {
    case .openApp, .focusApp, .focusElement, .scrollUp, .scrollDown, .stop:
      .low
    case .pressKey(.returnKey), .pressKey(.space), .closeWindow:
      .high
    case .pressKey:
      .low
    case .clickElement(_, let label) where isHighRiskLabel(label):
      .high
    case .clickElement, .typeText:
      .medium
    }
  }

  private func targetsSecureField(_ action: AutomationAction, state: DesktopState) -> Bool {
    let elementID: String?
    switch action {
    case .typeText(let id, _), .focusElement(let id, _), .clickElement(let id, _): elementID = id
    default: elementID = nil
    }
    guard let elementID, let element = state.elements.first(where: { $0.id == elementID }) else {
      return false
    }
    let description = [element.role, element.subrole, element.label].compactMap { $0 }.joined(
      separator: " "
    ).lowercased()
    return description.contains("secure") || description.contains("password")
  }

  private func containsBlockedIntent(_ action: AutomationAction) -> Bool {
    let text: String
    switch action {
    case .clickElement(_, let label): text = label ?? ""
    case .typeText(_, let value): text = value
    default: return false
    }
    let normalized = text.lowercased()
    let blockedTerms = [
      "buy now", "purchase", "place order", "delete permanently", "empty trash", "erase disk",
    ]
    return blockedTerms.contains(where: normalized.contains)
  }

  private func isHighRiskLabel(_ label: String?) -> Bool {
    guard let normalized = label?.lowercased() else { return false }
    let terms = ["send", "submit", "post", "publish", "confirm", "approve", "delete", "remove", "trash", "erase"]
    return terms.contains(where: normalized.contains)
  }
}
