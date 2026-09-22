// Streams copied microphone buffers through FluidAudio's local Parakeet EOU model.
import AVFoundation
import Combine
import FluidAudio
import Foundation

// Buffers crossing actors are deep copies that no code mutates after capture.
extension AVAudioPCMBuffer: @unchecked @retroactive Sendable {}

/// Selects one of FluidAudio's separately cached Parakeet streaming encoders.
public enum SpeechRecognitionPreset: String, CaseIterable, Identifiable, Sendable {
  case fast160
  case balanced320
  case slow1280

  public var id: Self { self }
  public var title: String {
    switch self {
    case .fast160: "Fast · 160 ms"
    case .balanced320: "Balanced · 320 ms"
    case .slow1280: "Slow · 1280 ms"
    }
  }
  public var cacheFolder: String {
    switch self {
    case .fast160: "160ms"
    case .balanced320: "320ms"
    case .slow1280: "1280ms"
    }
  }
  public var isCached: Bool {
    guard let applicationSupport = FileManager.default.urls(
      for: .applicationSupportDirectory,
      in: .userDomainMask
    ).first else { return false }
    let root = applicationSupport
      .appendingPathComponent("FluidAudio/Models/parakeet-eou-streaming", isDirectory: true)
    let candidates = [
      root.appendingPathComponent("parakeet-eou-streaming/\(cacheFolder)", isDirectory: true),
      root.appendingPathComponent(cacheFolder, isDirectory: true),
    ]
    let required = [
      "streaming_encoder.mlmodelc",
      "decoder.mlmodelc",
      "joint_decision.mlmodelc",
      "vocab.json",
    ]
    return candidates.contains { folder in
      required.allSatisfy {
        FileManager.default.fileExists(atPath: folder.appendingPathComponent($0).path)
      }
    }
  }
  var chunkSize: StreamingChunkSize {
    switch self {
    case .fast160: .ms160
    case .balanced320: .ms320
    case .slow1280: .ms1280
    }
  }
}

public enum SpeechEvent: Sendable {
  case preparing(progress: Double?)
  case ready
  case transcript(String, isFinal: Bool)
  case failed(String)
}

/// Allows deterministic session tests without microphone or model access.
@MainActor
public protocol SpeechProviding: AnyObject {
  var onEvent: ((SpeechEvent) -> Void)? { get set }
  func start(preset: SpeechRecognitionPreset) async
  func finish() async
  func stop()
}

/// Isolates FluidAudio so capture ordering can be verified with deterministic engines.
protocol StreamingSpeechEngine: Sendable {
  func prepare(
    preset: SpeechRecognitionPreset,
    progress: @escaping @Sendable (Double) -> Void
  ) async throws
  func configure(
    partial: @escaping @Sendable (String) -> Void,
    eou: @escaping @Sendable (String) -> Void
  ) async throws
  func process(_ buffer: AVAudioPCMBuffer) async throws
  func finish() async throws -> String
  func cancel() async
}

/// Isolates microphone permissions and tap delivery for deterministic capture tests.
@MainActor
protocol SpeechAudioSource: AnyObject {
  func requestPermission() async -> Bool
  func start(onBuffer: @escaping @Sendable (AVAudioPCMBuffer) -> Void) throws
  func stop()
}

/// Owns a single FluidAudio manager so all buffers are processed in strict arrival order.
private actor ParakeetEngine: StreamingSpeechEngine {
  private var manager: StreamingEouAsrManager?
  private var preset: SpeechRecognitionPreset?

  func prepare(
    preset requested: SpeechRecognitionPreset,
    progress: @escaping @Sendable (Double) -> Void
  ) async throws {
    if manager != nil, preset == requested { progress(1); return }
    let next = StreamingEouAsrManager(
      chunkSize: requested.chunkSize,
      eouDebounceMs: 640
    )
    try await next.loadModels { update in progress(update.fractionCompleted) }
    try Task.checkCancellation()
    manager = next
    preset = requested
  }

  func configure(
    partial: @escaping @Sendable (String) -> Void,
    eou: @escaping @Sendable (String) -> Void
  ) async throws {
    guard let manager else { throw SpeechFailure.modelNotPrepared }
    await manager.reset()
    await manager.setPartialCallback(partial)
    await manager.setEouCallback(eou)
  }

  func process(_ buffer: AVAudioPCMBuffer) async throws {
    guard let manager else { throw SpeechFailure.modelNotPrepared }
    _ = try await manager.process(audioBuffer: buffer)
  }

  func finish() async throws -> String {
    guard let manager else { throw SpeechFailure.modelNotPrepared }
    return try await manager.finish()
  }

  func cancel() async {
    await manager?.reset()
  }
}

private enum SpeechFailure: LocalizedError {
  case modelNotPrepared
  case noMicrophone
  var errorDescription: String? {
    switch self {
    case .modelNotPrepared: "The Parakeet model is not ready."
    case .noMicrophone: "No microphone is available."
    }
  }
}

/// A copied Core Audio buffer is immutable after capture and safe to hand to the ordered actor.
private final class CopiedAudioBuffer: @unchecked Sendable {
  let value: AVAudioPCMBuffer
  init?(_ source: AVAudioPCMBuffer) {
    guard let copy = AVAudioPCMBuffer(
      pcmFormat: source.format,
      frameCapacity: source.frameLength
    ) else { return nil }
    copy.frameLength = source.frameLength
    let sourceBuffers = UnsafeMutableAudioBufferListPointer(
      UnsafeMutablePointer(mutating: source.audioBufferList)
    )
    let destinationBuffers = UnsafeMutableAudioBufferListPointer(copy.mutableAudioBufferList)
    guard sourceBuffers.count == destinationBuffers.count else { return nil }
    for index in sourceBuffers.indices {
      guard let sourceData = sourceBuffers[index].mData,
        let destinationData = destinationBuffers[index].mData else { continue }
      let count = min(
        Int(sourceBuffers[index].mDataByteSize),
        Int(destinationBuffers[index].mDataByteSize)
      )
      memcpy(destinationData, sourceData, count)
      destinationBuffers[index].mDataByteSize = UInt32(count)
    }
    value = copy
  }
}

/// Builds the realtime callback outside MainActor so Core Audio can invoke it safely.
func makeAudioTapHandler(
  deliver: @escaping @Sendable (AVAudioPCMBuffer) -> Void
) -> AVAudioNodeTapBlock {
  { buffer, _ in
    guard let copied = CopiedAudioBuffer(buffer) else { return }
    deliver(copied.value)
  }
}

/// Builds the stream yield closure outside MainActor for the same executor boundary.
private func makeAudioStreamHandler(
  continuation: AsyncStream<AVAudioPCMBuffer>.Continuation
) -> @Sendable (AVAudioPCMBuffer) -> Void {
  { buffer in continuation.yield(buffer) }
}

/// Produces immutable copies from the realtime audio tap.
@MainActor
private final class MicrophoneAudioSource: SpeechAudioSource {
  private let audioEngine = AVAudioEngine()
  private var tapInstalled = false

  func requestPermission() async -> Bool {
    await AVCaptureDevice.requestAccess(for: .audio)
  }

  func start(onBuffer: @escaping @Sendable (AVAudioPCMBuffer) -> Void) throws {
    let input = audioEngine.inputNode
    let format = input.outputFormat(forBus: 0)
    guard format.sampleRate > 0, format.channelCount > 0 else { throw SpeechFailure.noMicrophone }
    input.installTap(
      onBus: 0,
      bufferSize: 1_024,
      format: format,
      block: makeAudioTapHandler(deliver: onBuffer)
    )
    tapInstalled = true
    audioEngine.prepare()
    try audioEngine.start()
  }

  func stop() {
    if audioEngine.isRunning { audioEngine.stop() }
    if tapInstalled { audioEngine.inputNode.removeTap(onBus: 0); tapInstalled = false }
  }
}

/// Captures microphone audio only after the selected model is ready.
@MainActor
public final class FluidAudioSpeechRecognizer: ObservableObject, SpeechProviding {
  @Published public private(set) var isRecording = false
  public var onEvent: ((SpeechEvent) -> Void)?
  private let engine: any StreamingSpeechEngine
  private let audioSource: any SpeechAudioSource
  private var generation = UUID()
  private var processingTask: Task<Void, Never>?
  private var continuation: AsyncStream<AVAudioPCMBuffer>.Continuation?

  public convenience init() {
    self.init(engine: ParakeetEngine(), audioSource: MicrophoneAudioSource())
  }

  init(engine: any StreamingSpeechEngine, audioSource: any SpeechAudioSource) {
    self.engine = engine
    self.audioSource = audioSource
  }

  /// Prepares, asks for microphone access, then starts one ordered stream consumer.
  public func start(preset: SpeechRecognitionPreset) async {
    stop()
    let id = generation
    guard await prepare(preset: preset, generation: id) else { return }
    let microphone = await audioSource.requestPermission()
    guard generation == id, !Task.isCancelled else { return }
    guard microphone else { fail("Microphone permission is required.", generation: id); return }

    do {
      try await engine.configure(
        partial: { [weak self] text in
          Task { @MainActor in self?.publish(text, isFinal: false, generation: id) }
        },
        eou: { [weak self] text in
          Task { @MainActor in self?.publish(text, isFinal: true, generation: id) }
        }
      )
      guard generation == id, !Task.isCancelled else { return }
      let pair = AsyncStream<AVAudioPCMBuffer>.makeStream(
        bufferingPolicy: .unbounded
      )
      continuation = pair.continuation
      processingTask = Task { [weak self, engine] in
        do {
          for await buffer in pair.stream {
            try Task.checkCancellation()
            try await engine.process(buffer)
          }
        } catch is CancellationError {
        } catch {
          self?.fail(error.localizedDescription, generation: id)
        }
      }
      try audioSource.start(
        onBuffer: makeAudioStreamHandler(continuation: pair.continuation)
      )
      isRecording = true
      onEvent?(.ready)
    } catch {
      fail("The microphone could not start: \(error.localizedDescription)", generation: id)
    }
  }

  /// Drains captured buffers and commits FluidAudio's best transcript once.
  public func finish() async {
    let id = generation
    guard isRecording else { return }
    audioSource.stop()
    continuation?.finish()
    continuation = nil
    let pending = processingTask
    processingTask = nil
    await pending?.value
    guard generation == id, !Task.isCancelled else { return }
    do {
      let transcript = try await engine.finish()
      guard generation == id, !Task.isCancelled else { return }
      isRecording = false
      publish(transcript, isFinal: true, generation: id)
    } catch is CancellationError {
    } catch {
      fail("Transcription could not finish: \(error.localizedDescription)", generation: id)
    }
  }

  /// Stop invalidates every callback and discards partial speech without finalizing it.
  public func stop() {
    generation = UUID()
    audioSource.stop()
    continuation?.finish()
    continuation = nil
    processingTask?.cancel()
    processingTask = nil
    isRecording = false
    Task { await engine.cancel() }
  }

  private func prepare(
    preset: SpeechRecognitionPreset,
    generation id: UUID
  ) async -> Bool {
    onEvent?(.preparing(progress: nil))
    do {
      try await engine.prepare(preset: preset) { [weak self] progress in
        Task { @MainActor in
          guard let self, self.generation == id else { return }
          self.onEvent?(.preparing(progress: progress))
        }
      }
      guard generation == id, !Task.isCancelled else { return false }
      return true
    } catch is CancellationError {
      return false
    } catch {
      fail("Parakeet model preparation failed: \(error.localizedDescription)", generation: id)
      return false
    }
  }

  private func publish(_ text: String, isFinal: Bool, generation id: UUID) {
    guard generation == id else { return }
    onEvent?(.transcript(text, isFinal: isFinal))
  }

  private func fail(_ message: String, generation id: UUID) {
    guard generation == id else { return }
    stop()
    onEvent?(.failed(message))
  }
}

/// Streams a known audio file through the same Parakeet configuration for opt-in accuracy tests.
public enum FluidAudioFileTranscriber {
  public static func transcribe(
    fileURL: URL,
    preset: SpeechRecognitionPreset = .balanced320,
    progress: @escaping @Sendable (Double) -> Void = { _ in }
  ) async throws -> String {
    let manager = StreamingEouAsrManager(
      chunkSize: preset.chunkSize,
      eouDebounceMs: 640
    )
    try await manager.loadModels { update in progress(update.fractionCompleted) }
    let file = try AVAudioFile(forReading: fileURL)
    let capacity: AVAudioFrameCount = 4_096
    while file.framePosition < file.length {
      try Task.checkCancellation()
      guard let buffer = AVAudioPCMBuffer(
        pcmFormat: file.processingFormat,
        frameCapacity: capacity
      ) else { throw SpeechFailure.noMicrophone }
      try file.read(into: buffer, frameCount: capacity)
      guard buffer.frameLength > 0 else { break }
      _ = try await manager.process(audioBuffer: buffer)
    }
    return try await manager.finish()
  }
}
