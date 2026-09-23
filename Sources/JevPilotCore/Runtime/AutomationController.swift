// Coordinates cancellable automation and checkpoints compact run records.
import Combine
import Foundation

/// Owns one bounded run; generation checks isolate every asynchronous continuation.
@MainActor
public final class AutomationController: ObservableObject {
  public enum Status: Equatable {
    case idle, running(step: Int), awaitingConfirmation, awaitingAppChoice, completed, stopped, rejected, blocked(String), failed(String)
    public var label: String {
      switch self {
      case .idle: "Ready"
      case .running(let step): "Running step \(step)"
      case .awaitingConfirmation: "Needs confirmation"
      case .awaitingAppChoice: "Choose an app"
      case .completed: "Completed"
      case .stopped: "Stopped"
      case .rejected: "Action rejected"
      case .blocked(let reason): reason
      case .failed(let reason): reason
      }
    }
    public var isActive: Bool {
      switch self { case .running, .awaitingConfirmation, .awaitingAppChoice: true; default: false }
    }
  }

  @Published public private(set) var status: Status = .idle
  @Published public private(set) var transcript = ""
  @Published public private(set) var latestState: DesktopState?
  @Published public private(set) var availableActions: [ActionCandidate] = []
  @Published public private(set) var latestDecision: ActionDecision?
  @Published public private(set) var pendingConfirmation: PendingConfirmation?
  @Published public private(set) var appChoices: [ActionCandidate] = []
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
  private let prepareTarget: @MainActor (Int32?) async throws -> Void
  private let maximumSteps: Int
  private let completionVerifier: any ActionCompletionVerifying
  private var pauseRequested = false
  private var selectedAppBundleID: String?
  private var pendingAppGoal: String?
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
    completionVerifier: any ActionCompletionVerifying = ImmediateActionCompletionVerifier(),
    prepareTarget: @escaping @MainActor (Int32?) async throws -> Void = { _ in }
  ) {
    self.perception = perception
    self.actionGenerator = actionGenerator
    self.decisionEngine = decisionEngine
    self.safetyPolicy = safetyPolicy
    self.executor = executor
    self.prepareTarget = prepareTarget
    self.maximumSteps = maximumSteps
    self.store = store ?? RunStore(inMemory: true)
    self.completionVerifier = completionVerifier
  }

  /// Starting from either surface uses the same single-run boundary.
  public func run(goal: String, targetProcessIdentifier: Int32? = nil,
    queuedAt: Date? = nil, speechMilliseconds: Int? = nil) {
    let goal = goal.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !goal.isEmpty, !status.isActive else { return }
    generation = UUID()
    pauseRequested = false
    selectedAppBundleID = nil
    pendingAppGoal = nil
    appChoices = []
    let id = generation
    transcript = goal
    history = []
    debugEvents = []
    latestState = nil
    latestDecision = nil
    availableActions = []
    currentRun = RunRecord(command: goal, pricing: pricing)
    if let speechMilliseconds {
      currentRun?.timings = [StageTiming(stage: "speech", step: 0, milliseconds: speechMilliseconds)]
    }
    if let queuedAt {
      if currentRun?.timings == nil { currentRun?.timings = [] }
      currentRun?.timings?.append(StageTiming(stage: "queue_wait", step: 0,
        milliseconds: max(0, Int(Date().timeIntervalSince(queuedAt) * 1_000))))
    }
    checkpoint()
    if let reason = safetyPolicy.blockedReason(forGoal: goal) {
      finish(.blocked, status: .blocked(reason), detail: reason)
      return
    }
    status = .running(step: 1)
    task = Task { [weak self] in await self?.runLoop(goal: goal, startingAt: 1, generation: id, targetProcessIdentifier: targetProcessIdentifier) }
  }

  /// Invalidates work before cancellation so even a cancellation-ignoring provider is harmless.
  public func cancel() {
    generation = UUID()
    task?.cancel()
    task = nil
    pendingConfirmation = nil
    appChoices = []
    pendingAppGoal = nil
    completionVerifier.cancel()
    if currentRun?.outcome == nil, currentRun != nil { finish(.stopped, status: .stopped) }
  }

  /// A correction lets a native call finish, then prevents the next decision/effect.
  public func requestPauseAfterCurrentEffect() {
    guard status.isActive else { return }
    pauseRequested = true
    if case .awaitingConfirmation = status { cancel() }
    if case .awaitingAppChoice = status { cancel() }
  }

  /// The user resolves duplicate installed-app names; Jev still chooses the action.
  public func chooseApplication(bundleIdentifier: String) {
    guard case .awaitingAppChoice = status, let goal = pendingAppGoal,
      appChoices.contains(where: {
        switch $0.action {
        case .openApp(let id, _), .focusApp(let id, _): id == bundleIdentifier
        default: false
        }
      }) else { return }
    selectedAppBundleID = bundleIdentifier
    pendingAppGoal = nil
    appChoices = []
    let id = generation
    status = .running(step: 1)
    task = Task { [weak self] in await self?.runLoop(goal: goal, startingAt: 1, generation: id) }
  }

  /// Restores the reviewed app and validates the selected action once more.
  public func confirmPendingAction() {
    guard let pending = pendingConfirmation, let original = latestState else { return }
    pendingConfirmation = nil
    let id = generation
    status = .running(step: pending.nextStep - 1)
    task = Task { [weak self] in
      guard let self, self.isCurrent(id) else { return }
      do {
        try await self.prepareTarget(original.activeApplication?.processIdentifier)
        guard self.isCurrent(id) else { return }
        guard let fresh = try self.validatedState(for: pending.decision.candidate.action, goal: pending.goal, selectedState: original) else {
          self.finish(.failed, status: .failed("The target changed. Start a new command."), detail: "Confirmation target changed.")
          return
        }
        let assessment = self.safetyPolicy.assess(action: pending.decision.candidate.action, confidence: pending.decision.confidence, state: fresh)
        if assessment.disposition == .deny {
          self.finish(.blocked, status: .blocked(assessment.reason), detail: assessment.reason)
          return
        }
        guard self.isCurrent(id) else { return }
        if await self.execute(pending.decision, generation: id, before: fresh) {
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

  private func isCurrent(_ id: UUID) -> Bool { generation == id && !Task.isCancelled && currentRun?.outcome == nil }

  /// A fresh snapshot replaces the native AX registry, so recheck the selected target before using its ID.
  private func validatedState(for action: AutomationAction, goal: String, selectedState: DesktopState) throws -> DesktopState? {
    let fresh = try perception.snapshot(recentActions: history)
    guard fresh.activeApplication?.processIdentifier == selectedState.activeApplication?.processIdentifier,
      fresh.focusedWindowID == selectedState.focusedWindowID,
      actionGenerator.candidates(for: goal, state: fresh).contains(where: { $0.action == action })
    else { return nil }

    switch action {
    case .clickElement(let id, _), .focusElement(let id, _), .typeText(let id, _),
      .activateMenu(let id, _), .selectTab(let id, _), .finderSelectItem(let id, _),
      .finderOpenItem(let id, _), .finderRenameItem(let id, _, _),
      .finderCopyItem(let id, _, _), .finderMoveItem(let id, _, _):
      guard let old = selectedState.elements.first(where: { $0.id == id }),
        let current = fresh.elements.first(where: { $0.id == id }), old == current
      else { return nil }
      if case .typeText = action {
        guard fresh.focusedElementID == id && current.isFocused else { return nil }
      }
    case .closeWindow(let id, _), .minimizeWindow(let id, _), .restoreWindow(let id, _),
      .enterFullScreen(let id, _), .exitFullScreen(let id, _):
      guard let old = selectedState.windows.first(where: { $0.id == id }),
        let current = fresh.windows.first(where: { $0.id == id }), old == current
      else { return nil }
    case .pressKey, .scrollUp, .scrollDown, .searchInApp, .terminalType, .terminalRun,
      .nextTab, .previousTab, .navigateBack, .navigateForward:
      guard fresh.focusedElementID == selectedState.focusedElementID else { return nil }
    case .openApp, .focusApp, .finderOpenFolder, .stop:
      break
    }
    return fresh
  }

  /// Observation and native execution stay on the main actor; network work suspends it.
  private func runLoop(goal: String, startingAt: Int, generation id: UUID, targetProcessIdentifier: Int32? = nil) async {
    guard isCurrent(id) else { return }
    guard startingAt <= maximumSteps else {
      finish(.failed, status: .failed("Reached the \(maximumSteps)-step safety limit."), detail: "Step limit reached.")
      return
    }
    for step in startingAt...maximumSteps {
      guard isCurrent(id) else { return }
      status = .running(step: step)
      do {
        if step == 1 {
          await actionGenerator.prepareInstalledApplications()
          guard isCurrent(id) else { return }
          do { try await prepareTarget(targetProcessIdentifier) }
          catch {
            if targetProcessIdentifier != nil { throw error }
            // App launch can start without a previous external target.
          }
          guard isCurrent(id) else { return }
        }
        let observationStarted = ContinuousClock.now
        let state = try perception.snapshot(recentActions: history)
        recordTiming("snapshot", step: step, since: observationStarted)
        latestState = state
        debugEvents.append(.init(kind: .observation, title: "Observed \(state.activeApplication?.name ?? "desktop")", detail: "\(state.windows.count) windows, \(state.elements.count) controls"))
        let generated = actionGenerator.candidates(for: goal, state: state)
        if step == 1, selectedAppBundleID == nil {
          let appActions = generated.filter {
            switch $0.action { case .openApp, .focusApp: true; default: false }
          }
          let ambiguous = Dictionary(grouping: appActions) { candidate -> String in
            switch candidate.action {
            case .openApp(_, let name), .focusApp(_, let name): name.lowercased()
            default: ""
            }
          }.values.first(where: { $0.count > 1 })
          if let ambiguous {
            appChoices = ambiguous
            pendingAppGoal = goal
            status = .awaitingAppChoice
            return
          }
        }
        let candidates = generated.filter { candidate in
          guard let selectedAppBundleID else { return true }
          switch candidate.action {
          case .openApp(let id, _), .focusApp(let id, _): return id == selectedAppBundleID
          default: return true
          }
        }
        availableActions = candidates
        debugEvents.append(.init(kind: .candidates, title: "\(candidates.count) valid actions", detail: candidates.map { "\($0.id): \($0.action.summary)" }.joined(separator: "\n")))
        let runID = currentRun!.id
        let decisionStarted = ContinuousClock.now
        let decision = try await decisionEngine.decide(goal: goal, state: state, candidates: candidates) { [weak self] metric in
          Task { @MainActor in self?.record(metric, runID: runID) }
        }
        guard isCurrent(id) else { return }
        recordTiming("jev", step: step, since: decisionStarted)
        if pauseRequested { finish(.stopped, status: .stopped, detail: "Paused after correction."); return }
        latestDecision = decision
        debugEvents.append(.init(kind: .decision, title: decision.candidate.action.summary, detail: "\(decision.model) · \(decision.latencyMilliseconds) ms · \(Int(decision.confidence * 100))% confidence"))
        if case .stop = decision.candidate.action {
          finish(.completed, status: .completed)
          return
        }
        let validationStarted = ContinuousClock.now
        guard let fresh = try validatedState(for: decision.candidate.action, goal: goal, selectedState: state) else {
          finish(.failed, status: .failed("The target changed. Start a new command."), detail: "Decision target changed before execution.")
          return
        }
        recordTiming("validation", step: step, since: validationStarted)
        let assessment = safetyPolicy.assess(action: decision.candidate.action, confidence: decision.confidence, state: fresh)
        debugEvents.append(.init(kind: .safety, title: assessment.disposition.rawValue, detail: assessment.reason))
        switch assessment.disposition {
        case .allow:
          if !(await execute(decision, generation: id, before: fresh)) { return }
        case .requireConfirmation:
          pendingConfirmation = .init(goal: goal, nextStep: step + 1, decision: decision, assessment: assessment)
          latestState = fresh
          addEvent(.init(kind: .confirmation, title: decision.candidate.action.summary, detail: assessment.reason))
          status = .awaitingConfirmation
          return
        case .deny:
          finish(.blocked, status: .blocked(assessment.reason), detail: assessment.reason)
          return
        }
        if pauseRequested { finish(.stopped, status: .stopped, detail: "Paused after correction."); return }
      } catch {
        guard isCurrent(id) else { return }
        debugEvents.append(.init(kind: .error, title: "Automation stopped", detail: error.localizedDescription))
        finish(.failed, status: .failed(error.localizedDescription), detail: "The run could not continue. Check permissions and provider access.")
        return
      }
    }
    if isCurrent(id) { finish(.failed, status: .failed("Reached the \(maximumSteps)-step safety limit."), detail: "Step limit reached.") }
  }

  private func execute(_ decision: ActionDecision, generation id: UUID, before: DesktopState? = nil) async -> Bool {
    guard isCurrent(id) else { return false }
    if case .stop = decision.candidate.action {
      finish(.completed, status: .completed)
      return false
    }
    let observation = before ?? latestState
    if let observation { completionVerifier.prepare(action: decision.candidate.action, before: observation) }
    let executionStarted = ContinuousClock.now
    let result = await executor.execute(decision.candidate.action)
    guard isCurrent(id) else { return false }
    let stepNumber: Int
    if case .running(let step) = status { stepNumber = step } else { stepNumber = 0 }
    recordTiming("execution", step: stepNumber, since: executionStarted)
    let verification: ExecutionResult
    if result.succeeded, let observation {
      let verificationStarted = ContinuousClock.now
      verification = await completionVerifier.verify(action: decision.candidate.action, before: observation, perception: perception)
      if isCurrent(id) { recordTiming("verification", step: stepNumber, since: verificationStarted) }
    } else {
      completionVerifier.cancel()
      verification = result
    }
    guard isCurrent(id) else { return false }
    let message = verification.succeeded ? result.message : verification.message
    history.append(.init(action: decision.candidate.action, succeeded: verification.succeeded, message: message))
    debugEvents.append(.init(kind: .execution, title: verification.succeeded ? "Verified" : "Execution unverified", detail: message))
    addEvent(.init(kind: .action, title: decision.candidate.action.summary, detail: message, succeeded: verification.succeeded))
    if !verification.succeeded { finish(.failed, status: .failed(message), detail: message) }
    return verification.succeeded
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
  private func recordTiming(_ stage: String, step: Int, since start: ContinuousClock.Instant) {
    let elapsed = start.duration(to: .now)
    let milliseconds = Int(elapsed.components.seconds * 1_000) + Int(elapsed.components.attoseconds / 1_000_000_000_000_000)
    if currentRun?.timings == nil { currentRun?.timings = [] }
    currentRun?.timings?.append(StageTiming(stage: stage, step: step, milliseconds: max(0, milliseconds)))
    checkpoint()
  }
  private func checkpoint() { if let currentRun { store.upsert(currentRun) } }
  private func finish(_ outcome: RunOutcome, status: Status, detail: String = "") {
    guard currentRun?.outcome == nil else { return }
    pendingConfirmation = nil
    appChoices = []
    pendingAppGoal = nil
    currentRun?.outcome = outcome
    currentRun?.endedAt = .now
    currentRun?.events.append(.init(kind: .status, title: outcome.rawValue.capitalized, detail: detail))
    checkpoint()
    self.status = status
  }
}
