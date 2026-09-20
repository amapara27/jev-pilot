// Coordinates the observe, decide, safety-check, and execute automation loop.
import Combine
import Foundation

/// Publishes automation state to SwiftUI and controls one cancellable run at a time.
@MainActor
public final class AutomationController: ObservableObject {
  /// Represents the controller's current lifecycle state for the UI.
  public enum Status: Equatable {
    case idle
    case running(step: Int)
    case awaitingConfirmation
    case completed
    case failed(String)

    public var label: String {
      switch self {
      case .idle: "Idle"
      case .running(let step): "Running step \(step)"
      case .awaitingConfirmation: "Waiting for confirmation"
      case .completed: "Completed"
      case .failed(let message): "Failed: \(message)"
      }
    }
  }

  @Published public private(set) var status: Status = .idle
  @Published public private(set) var transcript = ""
  @Published public private(set) var latestState: DesktopState?
  @Published public private(set) var availableActions: [ActionCandidate] = []
  @Published public private(set) var latestDecision: ActionDecision?
  @Published public private(set) var pendingConfirmation: PendingConfirmation?
  @Published public private(set) var history: [ActionRecord] = []
  @Published public private(set) var debugEvents: [DebugEvent] = []

  private let perception: DesktopPerceiving
  private let actionGenerator: ValidActionGenerator
  private let decisionEngine: any DecisionEngine
  private let safetyPolicy: SafetyPolicy
  private let executor: ActionExecuting
  private let maximumSteps: Int
  private var task: Task<Void, Never>?

  public init(
    perception: DesktopPerceiving,
    actionGenerator: ValidActionGenerator = ValidActionGenerator(),
    decisionEngine: any DecisionEngine,
    safetyPolicy: SafetyPolicy = SafetyPolicy(),
    executor: ActionExecuting,
    maximumSteps: Int = 12
  ) {
    self.perception = perception
    self.actionGenerator = actionGenerator
    self.decisionEngine = decisionEngine
    self.safetyPolicy = safetyPolicy
    self.executor = executor
    self.maximumSteps = maximumSteps
  }

  /// Starts a fresh run after rejecting empty or locally blocked goals.
  public func run(goal: String) {
    let trimmed = goal.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return }
    cancel()
    transcript = trimmed
    history = []
    debugEvents = []
    if let reason = safetyPolicy.blockedReason(forGoal: trimmed) {
      debugEvents.append(.init(kind: .safety, title: "Command blocked", detail: reason))
      status = .failed(reason)
      return
    }
    task = Task { [weak self] in
      await self?.runLoop(goal: trimmed, startingAt: 1)
    }
  }

  /// Cancels the active run and clears a pending confirmation.
  public func cancel() {
    task?.cancel()
    task = nil
    pendingConfirmation = nil
    if case .running = status { status = .idle }
  }

  /// Executes the pending reviewed action and resumes from fresh state.
  public func confirmPendingAction() {
    guard let pending = pendingConfirmation else { return }
    pendingConfirmation = nil
    task = Task { [weak self] in
      guard let self else { return }
      let shouldContinue = await self.execute(pending.decision)
      if shouldContinue {
        await self.runLoop(goal: pending.goal, startingAt: pending.nextStep)
      }
    }
  }

  /// Declines the pending action without executing it.
  public func rejectPendingAction() {
    guard let pending = pendingConfirmation else { return }
    debugEvents.append(
      .init(
        kind: .safety, title: "Action rejected", detail: pending.decision.candidate.action.summary))
    pendingConfirmation = nil
    status = .idle
  }

  public func requestAccessibilityPermission() {
    _ = perception.requestAccessibilityPermission(prompt: true)
  }

  /// Repeats one bounded observe-decide-act step until a terminal condition.
  private func runLoop(goal: String, startingAt: Int) async {
    guard startingAt <= maximumSteps else {
      status = .failed("Reached the \(maximumSteps)-step safety limit.")
      return
    }
    for step in startingAt...maximumSteps {
      guard !Task.isCancelled else { return }
      status = .running(step: step)
      do {
        let state = try perception.snapshot(recentActions: history)
        latestState = state
        debugEvents.append(
          .init(
            kind: .observation,
            title: "Observed \(state.activeApplication?.name ?? "desktop")",
            detail: "\(state.windows.count) windows, \(state.elements.count) interactive elements"
          ))

        let candidates = actionGenerator.candidates(for: goal, state: state)
        availableActions = candidates
        debugEvents.append(
          .init(
            kind: .candidates,
            title: "Generated \(candidates.count) valid actions",
            detail: candidates.map { "\($0.id): \($0.action.summary)" }.joined(separator: "\n")
          ))

        let decision = try await decisionEngine.decide(
          goal: goal, state: state, candidates: candidates)
        latestDecision = decision
        debugEvents.append(
          .init(
            kind: .decision,
            title: decision.candidate.action.summary,
            detail:
              "confidence \(decision.confidence.formatted(.percent.precision(.fractionLength(1)))) · \(decision.model) · \(decision.latencyMilliseconds) ms"
          ))

        let assessment = safetyPolicy.assess(
          action: decision.candidate.action,
          confidence: decision.confidence,
          state: state
        )
        debugEvents.append(
          .init(kind: .safety, title: assessment.disposition.rawValue, detail: assessment.reason))

        switch assessment.disposition {
        case .allow:
          if !(await execute(decision)) { return }
        case .requireConfirmation:
          pendingConfirmation = PendingConfirmation(
            goal: goal,
            nextStep: step + 1,
            decision: decision,
            assessment: assessment
          )
          status = .awaitingConfirmation
          return
        case .deny:
          status = .failed(assessment.reason)
          return
        }

        if case .stop = decision.candidate.action {
          status = .completed
          return
        }
        try await Task.sleep(for: .milliseconds(350))
      } catch is CancellationError {
        return
      } catch {
        debugEvents.append(
          .init(kind: .error, title: "Automation stopped", detail: error.localizedDescription))
        status = .failed(error.localizedDescription)
        return
      }
    }
    status = .failed("Reached the \(maximumSteps)-step safety limit.")
  }

  /// Performs a selected action and records the resulting history and debug event.
  private func execute(_ decision: ActionDecision) async -> Bool {
    let result = await executor.execute(decision.candidate.action)
    history.append(
      ActionRecord(
        action: decision.candidate.action, succeeded: result.succeeded, message: result.message))
    debugEvents.append(
      .init(
        kind: .execution, title: result.succeeded ? "Executed" : "Execution failed",
        detail: result.message))
    if !result.succeeded {
      status = .failed(result.message)
    }
    return result.succeeded
  }
}
