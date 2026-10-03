// Verifies provider telemetry against local HTTP fixtures, never the live API.
import Foundation
import XCTest
@testable import JevPilotCore

private final class MetricCollector: @unchecked Sendable {
  private let lock = NSLock()
  private var values: [RequestMetric] = []
  func append(_ metric: RequestMetric) { lock.withLock { values.append(metric) } }
  var metrics: [RequestMetric] { lock.withLock { values } }
}

private final class StubResponse: @unchecked Sendable {
  private let lock = NSLock()
  private var payload = Data()
  private var status = 200
  private var capturedBody = Data()
  func set(_ payload: String, status: Int = 200) {
    lock.withLock { self.payload = Data(payload.utf8); self.status = status }
  }
  func get() -> (Data, Int) { lock.withLock { (payload, status) } }
  var requestBody: Data { lock.withLock { capturedBody } }
  func capture(_ request: URLRequest) {
    var data = request.httpBody ?? Data()
    if data.isEmpty, let stream = request.httpBodyStream {
      stream.open()
      defer { stream.close() }
      var buffer = [UInt8](repeating: 0, count: 4096)
      while stream.hasBytesAvailable {
        let count = stream.read(&buffer, maxLength: buffer.count)
        guard count > 0 else { break }
        data.append(contentsOf: buffer.prefix(count))
      }
    }
    lock.withLock { capturedBody = data }
  }
}

private final class UsageURLProtocol: URLProtocol, @unchecked Sendable {
  static let response = StubResponse()
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    Self.response.capture(request)
    let (data, status) = Self.response.get()
    client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: nil)!, cacheStoragePolicy: .notAllowed)
    client?.urlProtocol(self, didLoad: data)
    client?.urlProtocolDidFinishLoading(self)
  }
  override func stopLoading() {}
}

@MainActor
final class ProviderUsageTests: XCTestCase {
  private func engine(key: String = "test-key") -> JevDecisionEngine {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [UsageURLProtocol.self]
    return JevDecisionEngine(session: URLSession(configuration: configuration), apiKeyProvider: { key })
  }
  private var candidates: [ActionCandidate] {
    [.init(id: "stop", action: .stop(reason: "done"), criterion: "finish")]
  }
  func testReportedTokensAndRequestLifecycle() async throws {
    UsageURLProtocol.response.set(#"{"model":"jev-test","usage":{"input_tokens":123,"output_tokens":7},"answers":{"next_action":{"type":"choice","choice":"stop","confidence":1,"probabilities":{"stop":1}}}}"#)
    let collector = MetricCollector()
    _ = try await engine().decide(goal: "done", state: .init(), candidates: candidates, report: { collector.append($0) })
    let metrics = collector.metrics
    XCTAssertEqual(metrics.count, 2)
    XCTAssertEqual(metrics[0].id, metrics[1].id)
    XCTAssertFalse(metrics[0].isComplete)
    XCTAssertTrue(metrics[1].isComplete)
    XCTAssertEqual(metrics[1].inputTokens, 123)
    XCTAssertEqual(metrics[1].outputTokens, 7)
  }
  func testInvalidDecisionStillReportsUsage() async {
    UsageURLProtocol.response.set(#"{"model":"jev-test","usage":{"input_tokens":25,"output_tokens":4},"answers":{}}"#)
    let collector = MetricCollector()
    do {
      _ = try await engine().decide(goal: "done", state: .init(), candidates: candidates, report: { collector.append($0) })
      XCTFail("Invalid decision accepted")
    } catch {}
    XCTAssertEqual(collector.metrics.last?.inputTokens, 25)
    XCTAssertTrue(collector.metrics.last?.isComplete == true)
  }
  func testMissingUsageRemainsUnknownAndProviderBodyIsNotExposed() async {
    UsageURLProtocol.response.set(#"{"sensitive":"do not display this raw error"}"#, status: 401)
    let collector = MetricCollector()
    do {
      _ = try await engine().decide(goal: "done", state: .init(), candidates: candidates, report: { collector.append($0) })
      XCTFail("HTTP error accepted")
    } catch { XCTAssertFalse(error.localizedDescription.contains("do not display")) }
    XCTAssertEqual(collector.metrics.count, 2)
    XCTAssertNil(collector.metrics.last?.inputTokens)
    XCTAssertNil(collector.metrics.last?.outputTokens)
  }
  func testMissingKeyDoesNotCountAsNetworkRequest() async {
    let collector = MetricCollector()
    do {
      _ = try await engine(key: "").decide(goal: "done", state: .init(), candidates: candidates, report: { collector.append($0) })
      XCTFail("Empty key accepted")
    } catch {}
    XCTAssertTrue(collector.metrics.isEmpty)
  }

  func testJevReceivesVerbatimGoalTypingSemanticsAndBoundedRedactedText() async throws {
    UsageURLProtocol.response.set(#"{"model":"jev-test","answers":{"next_action":{"type":"choice","choice":"stop","confidence":1,"probabilities":{"stop":1}}}}"#)
    let fullText = String(repeating: "x", count: 500) + "local-only-tail"
    let state = DesktopState(elements: [
      .init(id: "body", role: "AXTextArea", value: fullText, textSelection: .init(location: 500, length: 0)),
      .init(id: "password", role: "AXTextField", subrole: "AXSecureTextField", value: "secret-value"),
    ])
    let goal = "please type open Notes and write Hello"
    _ = try await engine().decide(goal: goal, state: state, candidates: candidates)
    let body = try XCTUnwrap(JSONSerialization.jsonObject(with: UsageURLProtocol.response.requestBody) as? [String: Any])
    let sentState = try XCTUnwrap(body["state"] as? [String: Any])
    XCTAssertEqual(sentState["goal"] as? String, goal)
    let desktop = try XCTUnwrap(sentState["desktop"] as? [String: Any])
    let elements = try XCTUnwrap(desktop["elements"] as? [[String: Any]])
    XCTAssertEqual(elements[0]["value"] as? String, String(repeating: "x", count: 240))
    XCTAssertEqual(elements[1]["value"] as? String, "<redacted>")
    XCTAssertEqual(state.elements[0].value, fullText)
    let questions = try XCTUnwrap(body["questions"] as? [String: [String: Any]])
    let instructions = try XCTUnwrap(questions["next_action"]?["instructions"] as? String)
    XCTAssertTrue(instructions.contains("Infer the user's intent"))
    XCTAssertTrue(instructions.contains("Focus the intended editable field first"))
    XCTAssertTrue(instructions.contains("never authorize Return"))
  }

  func testLiveJevProbeWhenExplicitlyEnabled() async throws {
    let environment = ProcessInfo.processInfo.environment
    guard environment["JEV_LIVE_JEV_TEST"] == "1",
      let key = environment["TYPESAFE_API_KEY"], !key.isEmpty else {
      throw XCTSkip("Set JEV_LIVE_JEV_TEST=1 and TYPESAFE_API_KEY to call the live provider.")
    }
    let decision = try await JevDecisionEngine(apiKeyProvider: { key }).decide(
      goal: "Stop because this is a dry-run provider validation.",
      state: .init(),
      candidates: candidates
    )
    XCTAssertEqual(decision.candidate.id, "stop")
    XCTAssertEqual(Set(decision.probabilities.keys), ["stop"])
  }
}
