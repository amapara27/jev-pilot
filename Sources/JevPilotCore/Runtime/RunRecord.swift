// Stores compact, local run history and provider usage without desktop snapshots.
import Foundation

public enum RunOutcome: String, Codable, Sendable, CaseIterable {
  case completed, stopped, rejected, blocked, failed, interrupted
}

/// Rates are captured per run so future pricing edits do not rewrite old estimates.
public struct TokenPricing: Codable, Equatable, Sendable {
  public var inputPerMillion: Double
  public var outputPerMillion: Double
  public init(inputPerMillion: Double = 0.042, outputPerMillion: Double = 0) {
    self.inputPerMillion = inputPerMillion
    self.outputPerMillion = outputPerMillion
  }
  public func estimate(_ metric: RequestMetric) -> Double? {
    guard let input = metric.inputTokens, let output = metric.outputTokens,
      input >= 0, output >= 0, inputPerMillion.isFinite, outputPerMillion.isFinite,
      inputPerMillion >= 0, outputPerMillion >= 0 else { return nil }
    return (Double(input) * inputPerMillion + Double(output) * outputPerMillion) / 1_000_000
  }
}

/// One actual provider request, including unsuccessful or cancelled requests.
public struct RequestMetric: Codable, Equatable, Sendable, Identifiable {
  public var id = UUID()
  public var timestamp = Date()
  public var latencyMilliseconds: Int
  public var inputTokens: Int?
  public var outputTokens: Int?
  public var isComplete: Bool
  public init(latencyMilliseconds: Int, inputTokens: Int? = nil, outputTokens: Int? = nil, isComplete: Bool = true) {
    self.latencyMilliseconds = latencyMilliseconds
    self.inputTokens = inputTokens
    self.outputTokens = outputTokens
    self.isComplete = isComplete
  }
}

/// A human-readable event, intentionally separate from verbose runtime diagnostics.
public struct RunEvent: Codable, Equatable, Sendable, Identifiable {
  public enum Kind: String, Codable, Sendable { case action, confirmation, status }
  public var id = UUID()
  public var timestamp = Date()
  public var kind: Kind
  public var title: String
  public var detail: String
  public var succeeded: Bool?
  public init(kind: Kind, title: String, detail: String = "", succeeded: Bool? = nil) {
    self.kind = kind
    self.title = title
    self.detail = detail
    self.succeeded = succeeded
  }
}

/// The persisted record contains no AX state, raw provider payloads, or audio.
public struct RunRecord: Codable, Equatable, Sendable, Identifiable {
  public var id = UUID()
  public var command: String
  public var startedAt = Date()
  public var endedAt: Date?
  public var outcome: RunOutcome?
  public var events: [RunEvent] = []
  public var requests: [RequestMetric] = []
  public var pricing: TokenPricing
  public init(command: String, pricing: TokenPricing = .init()) {
    self.command = command
    self.pricing = pricing
  }
  public var duration: TimeInterval { max(0, (endedAt ?? .now).timeIntervalSince(startedAt)) }
  public var actionCount: Int { events.filter { $0.kind == .action && $0.succeeded == true }.count }
  public var estimatedCost: Double { requests.compactMap(pricing.estimate).reduce(0, +) }
  public var hasIncompleteUsage: Bool { requests.contains { pricing.estimate($0) == nil } }
}

/// A lightweight aggregate scoped explicitly to the caller's retained run selection.
public struct UsageSummary: Sendable {
  public let runs: Int
  public let actions: Int
  public let requests: Int
  public let inputTokens: Int
  public let outputTokens: Int
  public let duration: TimeInterval
  public let averageLatency: Double?
  public let estimatedCost: Double
  public let incomplete: Bool
  public init(records: [RunRecord]) {
    let metrics = records.flatMap(\.requests)
    runs = records.count
    actions = records.reduce(0) { $0 + $1.actionCount }
    requests = metrics.count
    inputTokens = metrics.compactMap(\.inputTokens).reduce(0, +)
    outputTokens = metrics.compactMap(\.outputTokens).reduce(0, +)
    duration = records.reduce(0) { $0 + $1.duration }
    let complete = metrics.filter(\.isComplete)
    averageLatency = complete.isEmpty ? nil : Double(complete.reduce(0) { $0 + $1.latencyMilliseconds }) / Double(complete.count)
    estimatedCost = records.reduce(0) { $0 + $1.estimatedCost }
    incomplete = records.contains(where: \.hasIncompleteUsage)
  }
}
