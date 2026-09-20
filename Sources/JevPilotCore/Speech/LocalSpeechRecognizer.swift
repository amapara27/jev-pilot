import AVFoundation
import Combine
import Foundation
import Speech

@MainActor
public final class LocalSpeechRecognizer: NSObject, ObservableObject, SFSpeechRecognizerDelegate {
  @Published public private(set) var transcript = ""
  @Published public private(set) var isRecording = false
  @Published public private(set) var errorMessage: String?

  private let audioEngine = AVAudioEngine()
  private let recognizer: SFSpeechRecognizer?
  private var request: SFSpeechAudioBufferRecognitionRequest?
  private var task: SFSpeechRecognitionTask?
  private var tapInstalled = false

  public init(locale: Locale = .current) {
    recognizer = SFSpeechRecognizer(locale: locale)
    super.init()
    recognizer?.delegate = self
  }

  public func start() async {
    guard !isRecording else { return }
    errorMessage = nil
    let speechAuthorized = await requestSpeechAuthorization()
    guard speechAuthorized else {
      errorMessage = "Speech Recognition permission is required."
      return
    }
    guard await requestMicrophoneAuthorization() else {
      errorMessage = "Microphone permission is required."
      return
    }
    guard let recognizer, recognizer.isAvailable, recognizer.supportsOnDeviceRecognition else {
      errorMessage = "On-device speech recognition is unavailable for this language."
      return
    }

    task?.cancel()
    task = nil
    transcript = ""

    let request = SFSpeechAudioBufferRecognitionRequest()
    request.requiresOnDeviceRecognition = true
    request.shouldReportPartialResults = true
    self.request = request

    let input = audioEngine.inputNode
    let format = input.outputFormat(forBus: 0)
    input.installTap(onBus: 0, bufferSize: 1_024, format: format) { [weak request] buffer, _ in
      request?.append(buffer)
    }
    tapInstalled = true

    task = recognizer.recognitionTask(with: request) { [weak self] result, error in
      Task { @MainActor in
        guard let self else { return }
        if let result {
          self.transcript = result.bestTranscription.formattedString
          if result.isFinal { self.stop() }
        }
        if let error {
          self.errorMessage = error.localizedDescription
          self.stop()
        }
      }
    }

    do {
      audioEngine.prepare()
      try audioEngine.start()
      isRecording = true
    } catch {
      input.removeTap(onBus: 0)
      tapInstalled = false
      errorMessage = error.localizedDescription
    }
  }

  public func stop() {
    if audioEngine.isRunning { audioEngine.stop() }
    if tapInstalled {
      audioEngine.inputNode.removeTap(onBus: 0)
      tapInstalled = false
    }
    request?.endAudio()
    request = nil
    task = nil
    isRecording = false
  }

  private func requestSpeechAuthorization() async -> Bool {
    await withCheckedContinuation { continuation in
      SFSpeechRecognizer.requestAuthorization { status in
        continuation.resume(returning: status == .authorized)
      }
    }
  }

  private func requestMicrophoneAuthorization() async -> Bool {
    await AVCaptureDevice.requestAccess(for: .audio)
  }
}
