// Defines the one-shot Jev boundary that observes and decides without executing.
import Foundation

/// Keeps every dry-run artifact in memory for inspection without creating history.
public struct JevGoalProbeResult: Equatable, Sendable {
  public let goal: String
  public let desktopState: DesktopState
  public let candidates: [ActionCandidate]
  public let decision: ActionDecision
  public let requestMetric: RequestMetric?

  public init(
    goal: String,
    desktopState: DesktopState,
    candidates: [ActionCandidate],
    decision: ActionDecision,
    requestMetric: RequestMetric? = nil
  ) {
    self.goal = goal
    self.desktopState = desktopState
    self.candidates = candidates
    self.decision = decision
    self.requestMetric = requestMetric
  }
}

/// Makes the dry-run injectable and independently testable from speech capture.
@MainActor
public protocol GoalProbing: AnyObject {
  func probe(goal: String) async throws -> JevGoalProbeResult
}

/// A lock-backed callback box keeps synchronous request telemetry ephemeral.
private final class ProbeMetricBox: @unchecked Sendable {
  private let lock = NSLock()
  private var metric: RequestMetric?
  func record(_ next: RequestMetric) { lock.withLock { metric = next } }
  func read() -> RequestMetric? { lock.withLock { metric } }
}

/// Restores the target, captures once, generates once, and asks Jev exactly once.
@MainActor
public final class JevGoalProbe: GoalProbing {
  private let perception: any DesktopPerceiving
  private let generator: ValidActionGenerator
  private let decisionEngine: any DecisionEngine
  private let readiness: @MainActor () -> String?
  private let prepareTarget: @MainActor () async throws -> Void

  public init(
    perception: any DesktopPerceiving,
    generator: ValidActionGenerator = ValidActionGenerator(),
    decisionEngine: any DecisionEngine,
    readiness: @escaping @MainActor () -> String? = { nil },
    prepareTarget: @escaping @MainActor () async throws -> Void = {}
  ) {
    self.perception = perception
    self.generator = generator
    self.decisionEngine = decisionEngine
    self.readiness = readiness
    self.prepareTarget = prepareTarget
  }

  public func probe(goal: String) async throws -> JevGoalProbeResult {
    if let blocker = readiness() { throw ProbeError.unavailable(blocker) }
    try Task.checkCancellation()
    try await prepareTarget()
    try Task.checkCancellation()
    let state = try perception.snapshot(recentActions: [])
    let candidates = generator.candidates(for: goal, state: state)
    let metrics = ProbeMetricBox()
    let decision = try await decisionEngine.decide(
      goal: goal,
      state: state,
      candidates: candidates,
      report: { metrics.record($0) }
    )
    try Task.checkCancellation()
    return JevGoalProbeResult(
      goal: goal,
      desktopState: state,
      candidates: candidates,
      decision: decision,
      requestMetric: metrics.read()
    )
  }

  private enum ProbeError: LocalizedError {
    case unavailable(String)
    var errorDescription: String? {
      switch self { case .unavailable(let message): message }
    }
  }
}
