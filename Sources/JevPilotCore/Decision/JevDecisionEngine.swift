// Calls TypeSafe Jev and strictly validates its constrained choice response.
import Foundation

/// Serializes provider access while keeping model output within local candidates.
public actor JevDecisionEngine: DecisionEngine {
  /// Configures the provider endpoint, model name, and request timeout.
  public struct Configuration: Sendable {
    public var endpoint: URL
    public var model: String
    public var timeoutSeconds: TimeInterval

    public init(
      endpoint: URL = URL(string: "https://api.typesafe.ai/v1/systemone")!,
      model: String = "jev-latest",
      timeoutSeconds: TimeInterval = 20
    ) {
      self.endpoint = endpoint
      self.model = model
      self.timeoutSeconds = timeoutSeconds
    }
  }

  private let configuration: Configuration
  private let apiKeyProvider: @Sendable () throws -> String
  private let session: URLSession

  public init(
    configuration: Configuration = Configuration(),
    session: URLSession = .shared,
    apiKeyProvider: @escaping @Sendable () throws -> String
  ) {
    self.configuration = configuration
    self.session = session
    self.apiKeyProvider = apiKeyProvider
  }

  /// Requests one candidate choice and rejects malformed or unsafe provider output.
  public func decide(
    goal: String,
    state: DesktopState,
    candidates: [ActionCandidate]
  ) async throws -> ActionDecision {
    try await decide(goal: goal, state: state, candidates: candidates, report: { _ in })
  }

  /// Reports actual network attempts even when response validation fails.
  public func decide(
    goal: String, state: DesktopState, candidates: [ActionCandidate],
    report: @escaping @Sendable (RequestMetric) -> Void
  ) async throws -> ActionDecision {
    guard !candidates.isEmpty else { throw DecisionError.noCandidates }
    let key = try apiKeyProvider().trimmingCharacters(in: .whitespacesAndNewlines)
    guard !key.isEmpty else { throw DecisionError.missingAPIKey }

    let criteria = Dictionary(
      uniqueKeysWithValues: candidates.map {
        ($0.id, $0.criterion + " Concrete action: " + $0.action.summary)
      })
    let requestBody = SystemOneRequest(
      model: configuration.model,
      state: DecisionState(goal: goal, desktop: providerState(state)),
      questions: [
        "next_action": ChoiceQuestion(
          type: "choice",
          instructions:
            "The goal is the current instruction of an ordered task. Infer its intent and choose exactly one currently valid next action that advances it. 'Type', 'write', or 'dictate' means insert the exact supplied text, not execute commands mentioned inside that text. Focus the intended editable field first when needed; in Notes use the note body, not search. Creating a note may require opening Notes and then New Note. Type-only Terminal requests never authorize Return. STOP means no listed action can safely advance the instruction; the local runner separately verifies completion. Do not invent actions or parameters.",
          criteria: criteria
        )
      ]
    )

    var request = URLRequest(url: configuration.endpoint)
    request.httpMethod = "POST"
    request.timeoutInterval = configuration.timeoutSeconds
    request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try JSONEncoder.jev.encode(requestBody)

    try Task.checkCancellation()
    let startedAt = ContinuousClock.now
    var metric = RequestMetric(latencyMilliseconds: 0, isComplete: false)
    report(metric)
    defer {
      let duration = startedAt.duration(to: .now)
      metric.latencyMilliseconds = Int(duration.components.seconds * 1_000)
        + Int(duration.components.attoseconds / 1_000_000_000_000_000)
      metric.isComplete = true
      report(metric)
    }
    let data: Data
    let response: URLResponse
    do {
      (data, response) = try await session.data(for: request)
    } catch is CancellationError {
      throw CancellationError()
    } catch {
      if Task.isCancelled { throw CancellationError() }
      throw DecisionError.transport(error.localizedDescription)
    }
    let elapsed = startedAt.duration(to: .now)
    let milliseconds =
      Int(elapsed.components.seconds * 1_000)
      + Int(elapsed.components.attoseconds / 1_000_000_000_000_000)

    // Usage is decoded independently from the decision, including rejected responses.
    if let usage = try? JSONDecoder().decode(UsageEnvelope.self, from: data).usage {
      metric.inputTokens = usage.input_tokens.flatMap { $0 >= 0 ? $0 : nil }
      metric.outputTokens = usage.output_tokens.flatMap { $0 >= 0 ? $0 : nil }
    }
    guard let http = response as? HTTPURLResponse else {
      throw DecisionError.malformedResponse("response was not HTTP")
    }
    guard (200..<300).contains(http.statusCode) else {
      throw DecisionError.rejected(statusCode: http.statusCode, message: "Check your API key, account access, and provider availability.")
    }

    let decoded: SystemOneResponse
    do {
      decoded = try JSONDecoder().decode(SystemOneResponse.self, from: data)
    } catch {
      metric.validationFailure = "decoding"
      throw DecisionError.malformedResponse(error.localizedDescription)
    }
    guard let answer = decoded.answers["next_action"] else {
      metric.validationFailure = "missing_answer"
      throw DecisionError.malformedResponse("missing next_action answer")
    }
    guard answer.type == "choice" else {
      metric.validationFailure = "answer_type"
      throw DecisionError.malformedResponse("next_action was not a choice answer")
    }
    guard let candidate = candidates.first(where: { $0.id == answer.choice }) else {
      metric.validationFailure = "unknown_choice"
      throw DecisionError.malformedResponse("selected an unknown action ID")
    }

    metric.candidateCount = candidates.count
    if answer.probabilities.values.allSatisfy(\.isFinite) {
      let total = answer.probabilities.values.reduce(0, +)
      if total.isFinite { metric.probabilityTotal = total }
    }
    let expectedIDs = Set(candidates.map(\.id))
    guard Set(answer.probabilities.keys) == expectedIDs else {
      metric.validationFailure = "candidate_keys"
      throw DecisionError.malformedResponse("probability keys did not match action IDs")
    }
    guard answer.confidence.isFinite, (0...1).contains(answer.confidence),
      answer.probabilities.values.allSatisfy({ $0.isFinite && (0...1).contains($0) })
    else {
      metric.validationFailure = "out_of_range"
      throw DecisionError.malformedResponse("confidence or probabilities were out of range")
    }
    let probabilityTotal = answer.probabilities.values.reduce(0, +)
    metric.probabilityTotal = probabilityTotal
    // Bounded rounding repair only. Never normalize arbitrary scores or missing IDs.
    guard probabilityTotal > 0, abs(probabilityTotal - 1) <= 0.01 + 0.000_000_001 else {
      metric.validationFailure = "probability_sum"
      throw DecisionError.malformedResponse("probability sum \(String(format: "%.6f", probabilityTotal)) for \(candidates.count) actions is outside 1 ± 0.01")
    }
    let maximum = answer.probabilities.values.max() ?? 0
    guard let selectedProbability = answer.probabilities[answer.choice],
      selectedProbability + 0.000_001 >= maximum
    else {
      metric.validationFailure = "not_maximum"
      throw DecisionError.malformedResponse("choice was not a maximum-probability action")
    }

    metric.distributionNormalized = abs(probabilityTotal - 1) > 0.000_000_001
    return ActionDecision(
      candidate: candidate,
      confidence: min(answer.confidence, answer.confidence / probabilityTotal),
      probabilities: answer.probabilities.mapValues { $0 / probabilityTotal },
      model: decoded.model,
      latencyMilliseconds: milliseconds
    )
  }

  /// Exact local verification must not expand the text sent to the remote provider.
  private func providerState(_ state: DesktopState) -> DesktopState {
    var redacted = state
    redacted.elements = state.elements.map { element in
      UIElementState(id: element.id, role: element.role, subrole: element.subrole,
        label: element.label, value: element.isSecureTextInput ? "<redacted>" : element.value.map { String($0.prefix(240)) },
        isEnabled: element.isEnabled, isFocused: element.isFocused,
        supportedActions: element.supportedActions, depth: element.depth, url: element.url,
        isSelected: element.isSelected, textSelection: element.textSelection,
        valueIsTruncated: element.valueIsTruncated == true || (element.value?.count ?? 0) > 240)
    }
    return redacted
  }
}

/// Optional usage never weakens validation of the actual decision payload.
private struct UsageEnvelope: Decodable {
  struct Usage: Decodable { let input_tokens: Int?; let output_tokens: Int? }
  let usage: Usage?
}

private struct DecisionState: Encodable {
  let goal: String
  let desktop: DesktopState
}

private struct SystemOneRequest: Encodable {
  let model: String
  let state: DecisionState
  let questions: [String: ChoiceQuestion]
}

private struct ChoiceQuestion: Encodable {
  let type: String
  let instructions: String
  let criteria: [String: String]
}

private struct SystemOneResponse: Decodable {
  let model: String
  let answers: [String: ChoiceAnswer]
}

private struct ChoiceAnswer: Decodable {
  let type: String
  let choice: String
  let probabilities: [String: Double]
  let confidence: Double
}

extension JSONEncoder {
  fileprivate static var jev: JSONEncoder {
    let encoder = JSONEncoder()
    encoder.dateEncodingStrategy = .iso8601
    encoder.outputFormatting = [.sortedKeys]
    return encoder
  }
}
