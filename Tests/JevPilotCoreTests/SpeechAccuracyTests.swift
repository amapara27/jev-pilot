// Verifies deterministic WER math and keeps real model/network checks explicitly opt-in.
import Foundation
import XCTest
@testable import JevPilotCore

final class SpeechAccuracyTests: XCTestCase {
  func testNormalizationIgnoresCasePunctuationAndRepeatedWhitespace() {
    XCTAssertEqual(
      TranscriptionAccuracy.normalized("  Hello,   WORLD!  "),
      "hello world"
    )
    XCTAssertEqual(
      TranscriptionAccuracy.compare(reference: "Hello, world!", transcript: "hello world").wordErrorRate,
      0
    )
  }

  func testWerReportsEachOperation() {
    let substitution = TranscriptionAccuracy.compare(reference: "one two three", transcript: "one too three")
    XCTAssertEqual(substitution.substitutions, 1)
    XCTAssertEqual(substitution.wordErrorRate, 1.0 / 3.0, accuracy: 0.000_001)

    let insertion = TranscriptionAccuracy.compare(reference: "one two", transcript: "one extra two")
    XCTAssertEqual(insertion.insertions, 1)
    XCTAssertEqual(insertion.deletions, 0)

    let deletion = TranscriptionAccuracy.compare(reference: "one two three", transcript: "one three")
    XCTAssertEqual(deletion.deletions, 1)
    XCTAssertEqual(deletion.insertions, 0)
  }

  func testEmptyReferenceAndTranscriptAreDefined() {
    XCTAssertEqual(TranscriptionAccuracy.compare(reference: "", transcript: "").wordErrorRate, 0)
    let inserted = TranscriptionAccuracy.compare(reference: "", transcript: "unexpected words")
    XCTAssertEqual(inserted.insertions, 2)
    XCTAssertEqual(inserted.wordErrorRate, 1)
    let deleted = TranscriptionAccuracy.compare(reference: "expected words", transcript: "")
    XCTAssertEqual(deleted.deletions, 2)
    XCTAssertEqual(deleted.wordErrorRate, 1)
  }

  func testRealParakeetFileWhenExplicitlyEnabled() async throws {
    let environment = ProcessInfo.processInfo.environment
    guard let path = environment["JEV_STT_AUDIO"],
      let expected = environment["JEV_STT_EXPECTED"] else {
      throw XCTSkip("Set JEV_STT_AUDIO and JEV_STT_EXPECTED to run the local-model integration test.")
    }
    let preset = environment["JEV_STT_PRESET"]
      .flatMap(SpeechRecognitionPreset.init(rawValue:)) ?? .balanced320
    let transcript = try await FluidAudioFileTranscriber.transcribe(
      fileURL: URL(fileURLWithPath: path),
      preset: preset
    )
    XCTAssertFalse(transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    let maximum = Double(environment["JEV_STT_MAX_WER"] ?? "0.20") ?? 0.20
    let score = TranscriptionAccuracy.compare(reference: expected, transcript: transcript)
    XCTAssertLessThanOrEqual(score.wordErrorRate, maximum, "Transcript: \(transcript)")
  }
}
