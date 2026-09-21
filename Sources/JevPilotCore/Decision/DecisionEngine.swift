// Declares the decision-provider boundary and its user-facing errors.
import Foundation

/// Selects one candidate for a goal and desktop snapshot.
public protocol DecisionEngine: Sendable {
  func decide(
    goal: String,
    state: DesktopState,
    candidates: [ActionCandidate]
  ) async throws -> ActionDecision
  func decide(
    goal: String, state: DesktopState, candidates: [ActionCandidate],
    report: @escaping @Sendable (RequestMetric) -> Void
  ) async throws -> ActionDecision
}

extension DecisionEngine {
  /// Test and alternate providers can opt into telemetry without changing their base API.
  public func decide(
    goal: String, state: DesktopState, candidates: [ActionCandidate],
    report: @escaping @Sendable (RequestMetric) -> Void
  ) async throws -> ActionDecision {
    let start = Date()
    var metric = RequestMetric(latencyMilliseconds: 0, isComplete: false)
    report(metric)
    defer {
      metric.latencyMilliseconds = Int(Date().timeIntervalSince(start) * 1_000)
      metric.isComplete = true
      report(metric)
    }
    return try await decide(goal: goal, state: state, candidates: candidates)
  }
}

/// Describes configuration, transport, and response-validation failures.
public enum DecisionError: LocalizedError {
  case missingAPIKey
  case noCandidates
  case invalidEndpoint
  case transport(String)
  case rejected(statusCode: Int, message: String)
  case malformedResponse(String)

  public var errorDescription: String? {
    switch self {
    case .missingAPIKey:
      "No TypeSafe API key is configured. Add one in Settings or set TYPESAFE_API_KEY."
    case .noCandidates: "No valid actions were available."
    case .invalidEndpoint: "The configured TypeSafe endpoint is invalid."
    case .transport(let message): "TypeSafe request failed: \(message)"
    case .rejected(let statusCode, let message): "TypeSafe returned HTTP \(statusCode): \(message)"
    case .malformedResponse(let message): "TypeSafe returned an invalid decision: \(message)"
    }
  }
}
