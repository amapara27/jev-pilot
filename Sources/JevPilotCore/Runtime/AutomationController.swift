// Coordinates cancellable automation and checkpoints compact run records.
import Combine
import Foundation

/// Owns one bounded run; generation checks isolate every asynchronous continuation.
@MainActor
public final class AutomationController: ObservableObject {
  public enum Status: Equatable {
    case idle, running(step: Int), awaitingConfirmation, completed, stopped, rejected, blocked(String), failed(String)
    public var label: String {
      switch self {
      case .idle: "Ready"
      case .running(let step): "Running step \(step)"
      case .awaitingConfirmation: "Needs confirmation"
      case .completed: "Completed"
      case .stopped: "Stopped"
      case .rejected: "Action rejected"
      case .blocked(let reason): reason
      case .failed(let reason): reason
      }
    }
    public var isActive: Bool {
      switch self { case .running, .awaitingConfirmation: true; default: false }
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
  @Published public private(set) var currentRun: RunRecord?
  public let store: RunStore
  public var pricing = TokenPricing()
  private let perception: DesktopPerceiving
  private let actionGenerator: ValidActionGenerator
  private let decisionEngine: any DecisionEngine
  private let safetyPolicy: SafetyPolicy
  private let executor: ActionExecuting
  private let maximumSteps: Int
  private let stabilizationMilliseconds: Int
  private var generation = UUID()
  private var task: Task<Void, Never>?

  public init(
    perception: DesktopPerceiving,
    actionGenerator: ValidActionGenerator = ValidActionGenerator(),
    decisionEngine: any DecisionEngine,
    safetyPolicy: SafetyPolicy = SafetyPolicy(),
    executor: ActionExecuting,
    maximumSteps: Int = 12,
    store: RunStore? = nil,
    stabilizationMilliseconds: Int = 350
  ) {
    self.perception = perception
    self.actionGenerator = actionGenerator
    self.decisionEngine = decisionEngine
    self.safetyPolicy = safetyPolicy
    self.executor = executor
    self.maximumSteps = maximumSteps
    self.store = store ?? RunStore(inMemory: true)
    self.stabilizationMilliseconds = stabilizationMilliseconds
  }

  /// Starting from either surface uses the same single-run boundary.
  public func run(goal: String) {
    let goal = goal.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !goal.isEmpty, !status.isActive else { return }
    generation = UUID()
    let id = generation
    transcript = goal
    history = []
    debugEvents = []
    latestState = nil
    latestDecision = nil
    availableActions = []
    currentRun = RunRecord(command: goal, pricing: pricing)
    checkpoint()
    if let reason = safetyPolicy.blockedReason(forGoal: goal) {
      finish(.blocked, status: .blocked(reason), detail: reason)
      return
    }
    status = .running(step: 1)
    task = Task { [weak self] in await self?.runLoop(goal: goal, startingAt: 1, generation: id) }
  }

  /// Invalidates work before cancellation so even a cancellation-ignoring provider is harmless.
  public func cancel() {
    generation = UUID()
    task?.cancel()
    task = nil
    pendingConfirmation = nil
    if currentRun?.outcome == nil, currentRun != nil { finish(.stopped, status: .stopped) }
  }

  /// Validates the reviewed desktop again after the app restores the external target.
  public func confirmPendingAction() {
    guard let pending = pendingConfirmation, let original = latestState else { return }
    pendingConfirmation = nil
    let id = generation
    status = .running(step: pending.nextStep - 1)
    task = Task { [weak self] in
      guard let self, self.isCurrent(id) else { return }
      do {
        let fresh = try self.perception.snapshot(recentActions: self.history)
        guard Self.sameTarget(original, fresh) else {
          self.finish(.failed, status: .failed("The target changed. Start a new command."), detail: "Confirmation target changed.")
          return
        }
        guard self.isCurrent(id) else { return }
        if await self.execute(pending.decision, generation: id) {
          await self.runLoop(goal: pending.goal, startingAt: pending.nextStep, generation: id)
        }
      } catch {
        guard self.isCurrent(id) else { return }
        self.finish(.failed, status: .failed(error.localizedDescription), detail: "Could not validate the confirmation target.")
      }
    }
  }

  public func rejectPendingAction() {
    guard pendingConfirmation != nil else { return }
    generation = UUID()
    pendingConfirmation = nil
    finish(.rejected, status: .rejected)
  }
  public func requestAccessibilityPermission() { _ = perception.requestAccessibilityPermission(prompt: true) }

  private static func sameTarget(_ lhs: DesktopState, _ rhs: DesktopState) -> Bool {
    lhs.activeApplication == rhs.activeApplication && lhs.windows == rhs.windows
      && lhs.focusedWindowID == rhs.focusedWindowID && lhs.focusedElementID == rhs.focusedElementID
      && lhs.elements == rhs.elements
  }
  private func isCurrent(_ id: UUID) -> Bool { generation == id && !Task.isCancelled && currentRun?.outcome == nil }

  /// Observation and native execution stay on the main actor; network work suspends it.
  private func runLoop(goal: String, startingAt: Int, generation id: UUID) async {
    guard isCurrent(id) else { return }
    guard startingAt <= maximumSteps else {
      finish(.failed, status: .failed("Reached the \(maximumSteps)-step safety limit."), detail: "Step limit reached.")
      return
    }
    for step in startingAt...maximumSteps {
      guard isCurrent(id) else { return }
      status = .running(step: step)
      do {
        let state = try perception.snapshot(recentActions: history)
        latestState = state
        debugEvents.append(.init(kind: .observation, title: "Observed \(state.activeApplication?.name ?? "desktop")", detail: "\(state.windows.count) windows, \(state.elements.count) controls"))
        let candidates = actionGenerator.candidates(for: goal, state: state)
        availableActions = candidates
        debugEvents.append(.init(kind: .candidates, title: "\(candidates.count) valid actions", detail: candidates.map { "\($0.id): \($0.action.summary)" }.joined(separator: "\n")))
        let runID = currentRun!.id
        let decision = try await decisionEngine.decide(goal: goal, state: state, candidates: candidates) { [weak self] metric in
          Task { @MainActor in self?.record(metric, runID: runID) }
        }
        guard isCurrent(id) else { return }
        latestDecision = decision
        debugEvents.append(.init(kind: .decision, title: decision.candidate.action.summary, detail: "\(decision.model) · \(decision.latencyMilliseconds) ms · \(Int(decision.confidence * 100))% confidence"))
        let assessment = safetyPolicy.assess(action: decision.candidate.action, confidence: decision.confidence, state: state)
        debugEvents.append(.init(kind: .safety, title: assessment.disposition.rawValue, detail: assessment.reason))
        switch assessment.disposition {
        case .allow:
          if !(await execute(decision, generation: id)) { return }
        case .requireConfirmation:
          pendingConfirmation = .init(goal: goal, nextStep: step + 1, decision: decision, assessment: assessment)
          addEvent(.init(kind: .confirmation, title: decision.candidate.action.summary, detail: assessment.reason))
          status = .awaitingConfirmation
          return
        case .deny:
          finish(.blocked, status: .blocked(assessment.reason), detail: assessment.reason)
          return
        }
        try await Task.sleep(for: .milliseconds(stabilizationMilliseconds))
      } catch {
        guard isCurrent(id) else { return }
        debugEvents.append(.init(kind: .error, title: "Automation stopped", detail: error.localizedDescription))
        finish(.failed, status: .failed(error.localizedDescription), detail: "The run could not continue. Check permissions and provider access.")
        return
      }
    }
    if isCurrent(id) { finish(.failed, status: .failed("Reached the \(maximumSteps)-step safety limit."), detail: "Step limit reached.") }
  }

  private func execute(_ decision: ActionDecision, generation id: UUID) async -> Bool {
    guard isCurrent(id) else { return false }
    if case .stop = decision.candidate.action {
      finish(.completed, status: .completed)
      return false
    }
    let result = await executor.execute(decision.candidate.action)
    guard isCurrent(id) else { return false }
    history.append(.init(action: decision.candidate.action, succeeded: result.succeeded, message: result.message))
    debugEvents.append(.init(kind: .execution, title: result.succeeded ? "Executed" : "Execution failed", detail: result.message))
    addEvent(.init(kind: .action, title: decision.candidate.action.summary, detail: result.succeeded ? "Executed" : "Execution failed", succeeded: result.succeeded))
    if !result.succeeded { finish(.failed, status: .failed(result.message), detail: "Native action failed.") }
    return result.succeeded
  }

  private func record(_ metric: RequestMetric, runID: UUID) {
    if currentRun?.id == runID {
      if let index = currentRun?.requests.firstIndex(where: { $0.id == metric.id }) {
        guard currentRun?.requests[index].isComplete == false else { return }
        currentRun?.requests[index] = metric
      } else { currentRun?.requests.append(metric) }
      // Do not resurrect a completed run the user has already deleted.
      if store.records.contains(where: { $0.id == runID }) { checkpoint() }
    } else { store.appendMetric(metric, to: runID) }
  }
  private func addEvent(_ event: RunEvent) { currentRun?.events.append(event); checkpoint() }
  private func checkpoint() { if let currentRun { store.upsert(currentRun) } }
  private func finish(_ outcome: RunOutcome, status: Status, detail: String = "") {
    guard currentRun?.outcome == nil else { return }
    pendingConfirmation = nil
    currentRun?.outcome = outcome
    currentRun?.endedAt = .now
    currentRun?.events.append(.init(kind: .status, title: outcome.rawValue.capitalized, detail: detail))
    checkpoint()
    self.status = status
  }
}
