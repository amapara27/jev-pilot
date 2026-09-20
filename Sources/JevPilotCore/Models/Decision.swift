import Foundation

public struct ActionDecision: Codable, Equatable, Sendable {
  public let candidate: ActionCandidate
  public let confidence: Double
  public let probabilities: [String: Double]
  public let model: String
  public let latencyMilliseconds: Int

  public init(
    candidate: ActionCandidate,
    confidence: Double,
    probabilities: [String: Double],
    model: String,
    latencyMilliseconds: Int = 0
  ) {
    self.candidate = candidate
    self.confidence = confidence
    self.probabilities = probabilities
    self.model = model
    self.latencyMilliseconds = latencyMilliseconds
  }
}

public enum RiskLevel: String, Codable, Sendable {
  case low
  case medium
  case high
  case blocked
}

public enum SafetyDisposition: String, Codable, Sendable {
  case allow
  case requireConfirmation
  case deny
}

public struct SafetyAssessment: Codable, Equatable, Sendable {
  public let risk: RiskLevel
  public let disposition: SafetyDisposition
  public let reason: String

  public init(risk: RiskLevel, disposition: SafetyDisposition, reason: String) {
    self.risk = risk
    self.disposition = disposition
    self.reason = reason
  }
}

public struct ExecutionResult: Codable, Equatable, Sendable {
  public let succeeded: Bool
  public let message: String

  public init(succeeded: Bool, message: String) {
    self.succeeded = succeeded
    self.message = message
  }
}
