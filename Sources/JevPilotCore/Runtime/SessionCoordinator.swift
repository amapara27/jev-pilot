// Shares local transcription and desktop automation state across every app surface.
import Combine
import Foundation

/// Coordinates capture and hands one final goal to the bounded automation controller.
@MainActor
public final class SessionCoordinator: ObservableObject {
  public enum State: Equatable {
    case stopped
    case preparingModel(progress: Double?)
    case listening
    case askingJev
    case running(step: Int)
    case awaitingConfirmation
    case complete
    case rejected
    case blocked(String)
    case error(String)

    public var label: String {
      switch self {
      case .stopped: "Ready when you are"
      case .preparingModel(let progress):
        progress.map { "Preparing model · \(Int($0 * 100))%" } ?? "Preparing model"
      case .listening: "Listening"
      case .askingJev: "Asking Jev"
      case .running(let step): "Running step \(step)"
      case .awaitingConfirmation: "Needs confirmation"
      case .complete: "Complete"
      case .rejected: "Rejected"
      case .blocked(let message): message
      case .error(let message): message
      }
    }
    public var isActive: Bool {
      switch self {
      case .preparingModel, .listening, .askingJev, .running, .awaitingConfirmation: true
      case .stopped, .complete, .rejected, .blocked, .error: false
      }
    }
  }

  @Published public private(set) var state: State = .stopped
  @Published public private(set) var transcript = ""
  @Published public var showTranscript: Bool {
    didSet { defaults?.set(showTranscript, forKey: "showTranscript") }
  }
  @Published public var speechPreset: SpeechRecognitionPreset {
    didSet { defaults?.set(speechPreset.rawValue, forKey: "speechPreset") }
  }

  private let speech: any SpeechProviding
  public let controller: AutomationController
  private let defaults: UserDefaults?
  private var controllerStatusSubscription: AnyCancellable?
  private var generation = UUID()
  private var committed = false
  private var workTask: Task<Void, Never>?

  public init(
    controller: AutomationController,
    speech: any SpeechProviding,
    defaults: UserDefaults? = .standard
  ) {
    self.controller = controller
    self.speech = speech
    self.defaults = defaults
    showTranscript = defaults?.bool(forKey: "showTranscript") ?? false
    speechPreset = SpeechRecognitionPreset(
      rawValue: defaults?.string(forKey: "speechPreset") ?? ""
    ) ?? .balanced320
    controllerStatusSubscription = controller.$status.dropFirst().sink { [weak self] status in
      self?.receiveControllerStatus(status)
    }
  }

  /// Starts a one-shot capture; the speech provider prepares its model automatically.
  public func startListening() {
    guard !state.isActive else { return }
    beginCapture()
  }

  /// Flushes the current microphone stream and submits its best final transcript.
  public func finishListening() {
    guard state == .listening, !committed else { return }
    workTask = Task { [weak self] in
      guard let self else { return }
      await self.speech.finish()
    }
  }

  /// Typed text bypasses STT but uses the same bounded automation path.
  public func runTyped(_ command: String) {
    guard !state.isActive,
      !command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
    invalidate(clearTranscript: false)
    transcript = command
    submit(command, generation: generation)
  }

  /// Manual Stop cancels model, audio, and Jev work and never commits partial speech.
  public func stop() {
    if controller.status.isActive { controller.cancel() }
    invalidate(clearTranscript: state == .listening || isPreparing)
    state = .stopped
  }

  /// Confirmation is explicit; the controller restores and rechecks the desktop target.
  public func confirmPendingAction() { controller.confirmPendingAction() }
  public func rejectPendingAction() { controller.rejectPendingAction() }

  private var isPreparing: Bool {
    if case .preparingModel = state { return true }
    return false
  }

  private func invalidate(clearTranscript: Bool) {
    generation = UUID()
    workTask?.cancel()
    workTask = nil
    speech.stop()
    committed = false
    if clearTranscript { transcript = "" }
  }

  private func beginCapture() {
    invalidate(clearTranscript: true)
    let id = generation
    state = .preparingModel(progress: nil)
    speech.onEvent = { [weak self] event in self?.receive(event, generation: id) }
    workTask = Task { [weak self] in
      guard let self else { return }
      await self.speech.start(preset: self.speechPreset)
    }
  }

  private func receive(
    _ event: SpeechEvent,
    generation id: UUID
  ) {
    guard id == generation, !committed else { return }
    switch event {
    case .preparing(let progress):
      state = .preparingModel(progress: progress)
    case .ready:
      state = .listening
    case .failed(let message):
      invalidate(clearTranscript: false)
      state = .error(message)
    case .transcript(let text, let isFinal):
      guard state == .listening else { return }
      transcript = text
      guard isFinal else { return }
      let goal = text.trimmingCharacters(in: .whitespacesAndNewlines)
      guard !goal.isEmpty else {
        stop()
        return
      }
      // Preserve the model's words verbatim, removing only surrounding whitespace.
      transcript = goal
      submit(goal, generation: id)
    }
  }

  private func submit(_ goal: String, generation id: UUID) {
    guard generation == id, !committed else { return }
    committed = true
    speech.stop()
    state = .askingJev
    controller.run(goal: goal)
  }

  private func receiveControllerStatus(_ status: AutomationController.Status) {
    switch status {
    case .idle: break
    case .running(let step): state = .running(step: step)
    case .awaitingConfirmation: state = .awaitingConfirmation
    case .completed: committed = false; state = .complete
    case .stopped: committed = false; state = .stopped
    case .rejected: committed = false; state = .rejected
    case .blocked(let reason): committed = false; state = .blocked(reason)
    case .failed(let reason): committed = false; state = .error(reason)
    }
  }
}
