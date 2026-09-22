// Shares local transcription and one-shot Jev probing state across every app surface.
import Combine
import Foundation

public enum ListeningMode: String, CaseIterable, Identifiable, Sendable {
  case single, continuous
  public var id: Self { self }  public var title: String { self == .single ? "Single command" : "Continuous" }
}

/// Coordinates capture and probing while generation checks isolate every late callback.
@MainActor
public final class SessionCoordinator: ObservableObject {
  public enum State: Equatable {
    case stopped
    case preparingModel(progress: Double?)
    case listening
    case askingJev
    case complete
    case error(String)

    public var label: String {
      switch self {
      case .stopped: "Ready when you are"
      case .preparingModel(let progress):
        progress.map { "Preparing model · \(Int($0 * 100))%" } ?? "Preparing model"
      case .listening: "Listening"
      case .askingJev: "Asking Jev"
      case .complete: "Complete"
      case .error(let message): message
      }
    }
    public var isActive: Bool {
      switch self {
      case .preparingModel, .listening, .askingJev: true
      case .stopped, .complete, .error: false
      }
    }
  }

  @Published public private(set) var state: State = .stopped
  @Published public private(set) var transcript = ""
  @Published public private(set) var probeResult: JevGoalProbeResult?
  @Published public var mode: ListeningMode {
    didSet { defaults?.set(mode.rawValue, forKey: "listeningMode") }
  }
  @Published public var showTranscript: Bool {
    didSet { defaults?.set(showTranscript, forKey: "showTranscript") }
  }
  @Published public var speechPreset: SpeechRecognitionPreset {
    didSet { defaults?.set(speechPreset.rawValue, forKey: "speechPreset") }
  }

  private let speech: any SpeechProviding
  private let probe: any GoalProbing
  private let defaults: UserDefaults?
  private var generation = UUID()
  private var committed = false
  private var voiceSession = false
  private var workTask: Task<Void, Never>?

  public init(
    probe: any GoalProbing,
    speech: any SpeechProviding,
    defaults: UserDefaults? = .standard
  ) {
    self.probe = probe
    self.speech = speech
    self.defaults = defaults
    mode = ListeningMode(rawValue: defaults?.string(forKey: "listeningMode") ?? "") ?? .single
    showTranscript = defaults?.bool(forKey: "showTranscript") ?? false
    speechPreset = SpeechRecognitionPreset(
      rawValue: defaults?.string(forKey: "speechPreset") ?? ""
    ) ?? .balanced320
  }

  /// Downloads or opens the selected model without accessing the microphone.
  public func prepareSpeechModel() {
    guard !state.isActive else { return }
    invalidate(clearTranscript: false)
    let id = generation
    state = .preparingModel(progress: nil)
    speech.onEvent = { [weak self] event in self?.receive(event, generation: id, preparationOnly: true) }
    workTask = Task { [weak self] in
      guard let self else { return }
      await self.speech.prepare(preset: self.speechPreset)
    }
  }

  public func startListening() {
    guard !state.isActive else { return }
    voiceSession = true
    beginCapture()
  }

  /// Typed text bypasses STT but uses the identical one-shot observation and decision path.
  public func runTyped(_ command: String) {
    guard !state.isActive,
      !command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
    invalidate(clearTranscript: false)
    voiceSession = false
    transcript = command
    probeResult = nil
    submit(command, generation: generation)
  }

  /// Manual Stop cancels model, audio, and Jev work and never commits partial speech.
  public func stop() {
    invalidate(clearTranscript: state == .listening || isPreparing)
    voiceSession = false
    state = .stopped
  }

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
    probeResult = nil
    state = .preparingModel(progress: nil)
    speech.onEvent = { [weak self] event in self?.receive(event, generation: id, preparationOnly: false) }
    workTask = Task { [weak self] in
      guard let self else { return }
      await self.speech.start(preset: self.speechPreset)
    }
  }

  private func receive(
    _ event: SpeechEvent,
    generation id: UUID,
    preparationOnly: Bool
  ) {
    guard id == generation, !committed else { return }
    switch event {
    case .preparing(let progress):
      state = .preparingModel(progress: progress)
    case .ready:
      state = preparationOnly ? .complete : .listening
    case .failed(let message):
      invalidate(clearTranscript: false)
      state = .error(message)
    case .transcript(let text, let isFinal):
      guard !preparationOnly, state == .listening else { return }
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
    workTask = Task { [weak self] in
      guard let self else { return }
      do {
        let result = try await self.probe.probe(goal: goal)
        guard self.generation == id, !Task.isCancelled else { return }
        self.probeResult = result
        self.committed = false
        if self.voiceSession, self.mode == .continuous {
          self.beginCapture()
        } else {
          self.voiceSession = false
          self.state = .complete
        }
      } catch is CancellationError {
      } catch {
        guard self.generation == id else { return }
        self.voiceSession = false
        self.committed = false
        self.state = .error(error.localizedDescription)
      }
    }
  }
}
