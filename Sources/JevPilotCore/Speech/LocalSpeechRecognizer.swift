// Captures on-device speech; session identities reject callbacks after Stop or restart.
import AVFoundation
import Combine
import Foundation
import Speech

public enum SpeechEvent: Sendable {
  case transcript(String, isFinal: Bool)
  case failed(String)
}

/// Allows deterministic speech tests without microphone access.
@MainActor
public protocol SpeechProviding: AnyObject {
  var onEvent: ((SpeechEvent) -> Void)? { get set }
  func start() async
  func stop()
}

@MainActor
public final class LocalSpeechRecognizer: NSObject, ObservableObject, SpeechProviding {
  @Published public private(set) var transcript = ""
  @Published public private(set) var isRecording = false
  @Published public private(set) var errorMessage: String?
  public var onEvent: ((SpeechEvent) -> Void)?
  private let audioEngine = AVAudioEngine()
  private let recognizer: SFSpeechRecognizer?
  private var request: SFSpeechAudioBufferRecognitionRequest?
  private var recognitionTask: SFSpeechRecognitionTask?
  private var tapInstalled = false
  private var generation = UUID()

  public init(locale: Locale = .current) {
    recognizer = SFSpeechRecognizer(locale: locale)
    super.init()
  }

  /// Permission prompts are only reached by an explicit listening action.
  public func start() async {
    guard !Task.isCancelled else { return }
    stop()
    let id = generation
    transcript = ""
    errorMessage = nil
    let authorized = await withCheckedContinuation { continuation in
      SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0 == .authorized) }
    }
    guard id == generation, !Task.isCancelled else { return }
    guard authorized else { fail("Speech Recognition permission is required."); return }
    let microphone = await AVCaptureDevice.requestAccess(for: .audio)
    guard id == generation, !Task.isCancelled else { return }
    guard microphone else { fail("Microphone permission is required."); return }
    guard let recognizer, recognizer.isAvailable, recognizer.supportsOnDeviceRecognition else {
      fail("On-device speech recognition is unavailable for this language."); return
    }
    let request = SFSpeechAudioBufferRecognitionRequest()
    request.requiresOnDeviceRecognition = true
    request.shouldReportPartialResults = true
    self.request = request
    let input = audioEngine.inputNode
    let format = input.outputFormat(forBus: 0)
    guard format.sampleRate > 0, format.channelCount > 0 else { fail("No microphone is available."); return }
    input.installTap(onBus: 0, bufferSize: 1_024, format: format) { [weak request] buffer, _ in request?.append(buffer) }
    tapInstalled = true
    recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
      let text = result?.bestTranscription.formattedString
      let final = result?.isFinal ?? false
      let message = error?.localizedDescription
      Task { @MainActor in
        guard let self, self.generation == id else { return }
        if let text {
          self.transcript = text
          self.onEvent?(.transcript(text, isFinal: final))
        }
        // Submission may have invalidated this task during the callback above.
        if self.generation == id, let message { self.fail(message) }
      }
    }
    do {
      audioEngine.prepare()
      try audioEngine.start()
      isRecording = true
    } catch { fail("The microphone could not start: \(error.localizedDescription)") }
  }

  /// Cancel, rather than finalize, to prevent Stop from submitting partial speech.
  public func stop() {
    generation = UUID()
    if audioEngine.isRunning { audioEngine.stop() }
    if tapInstalled { audioEngine.inputNode.removeTap(onBus: 0); tapInstalled = false }
    recognitionTask?.cancel()
    recognitionTask = nil
    request?.endAudio()
    request = nil
    isRecording = false
  }
  private func fail(_ message: String) {
    stop()
    errorMessage = message
    onEvent?(.failed(message))
  }
}
