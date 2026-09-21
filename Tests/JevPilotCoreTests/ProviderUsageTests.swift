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
  func set(_ payload: String, status: Int = 200) {
    lock.withLock { self.payload = Data(payload.utf8); self.status = status }
  }
  func get() -> (Data, Int) { lock.withLock { (payload, status) } }
}

private final class UsageURLProtocol: URLProtocol, @unchecked Sendable {
  static let response = StubResponse()
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
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
}
