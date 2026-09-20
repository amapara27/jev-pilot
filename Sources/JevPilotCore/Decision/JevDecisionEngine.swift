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
    guard !candidates.isEmpty else { throw DecisionError.noCandidates }
    let key = try apiKeyProvider().trimmingCharacters(in: .whitespacesAndNewlines)
    guard !key.isEmpty else { throw DecisionError.missingAPIKey }

    let criteria = Dictionary(
      uniqueKeysWithValues: candidates.map {
        ($0.id, $0.criterion + " Concrete action: " + $0.action.summary)
      })
    let requestBody = SystemOneRequest(
      model: configuration.model,
      state: DecisionState(goal: goal, desktop: state),
      questions: [
        "next_action": ChoiceQuestion(
          type: "choice",
          instructions:
            "Choose exactly one currently valid next action that best advances the user's goal. Choose STOP only if the goal is complete or no listed action can safely advance it. Do not invent actions or parameters.",
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

    let startedAt = ContinuousClock.now
    let data: Data
    let response: URLResponse
    do {
      (data, response) = try await session.data(for: request)
    } catch {
      throw DecisionError.transport(error.localizedDescription)
    }
    let elapsed = startedAt.duration(to: .now)
    let milliseconds =
      Int(elapsed.components.seconds * 1_000)
      + Int(elapsed.components.attoseconds / 1_000_000_000_000_000)

    guard let http = response as? HTTPURLResponse else {
      throw DecisionError.malformedResponse("response was not HTTP")
    }
    guard (200..<300).contains(http.statusCode) else {
      let message = String(data: data.prefix(2_048), encoding: .utf8) ?? "no response body"
      throw DecisionError.rejected(statusCode: http.statusCode, message: message)
    }

    let decoded: SystemOneResponse
    do {
      decoded = try JSONDecoder().decode(SystemOneResponse.self, from: data)
    } catch {
      throw DecisionError.malformedResponse(error.localizedDescription)
    }
    guard let answer = decoded.answers["next_action"] else {
      throw DecisionError.malformedResponse("missing next_action answer")
    }
    guard answer.type == "choice" else {
      throw DecisionError.malformedResponse("next_action was not a choice answer")
    }
    guard let candidate = candidates.first(where: { $0.id == answer.choice }) else {
      throw DecisionError.malformedResponse("selected an unknown action ID")
    }

    let expectedIDs = Set(candidates.map(\.id))
    guard Set(answer.probabilities.keys) == expectedIDs else {
      throw DecisionError.malformedResponse("probability keys did not match action IDs")
    }
    guard answer.confidence.isFinite, (0...1).contains(answer.confidence),
      answer.probabilities.values.allSatisfy({ $0.isFinite && (0...1).contains($0) })
    else {
      throw DecisionError.malformedResponse("confidence or probabilities were out of range")
    }
    let probabilityTotal = answer.probabilities.values.reduce(0, +)
    guard abs(probabilityTotal - 1) <= 0.001 else {
      throw DecisionError.malformedResponse("probabilities did not sum to one")
    }
    let maximum = answer.probabilities.values.max() ?? 0
    guard let selectedProbability = answer.probabilities[answer.choice],
      selectedProbability + 0.000_001 >= maximum
    else {
      throw DecisionError.malformedResponse("choice was not a maximum-probability action")
    }

    return ActionDecision(
      candidate: candidate,
      confidence: answer.confidence,
      probabilities: answer.probabilities,
      model: decoded.model,
      latencyMilliseconds: milliseconds
    )
  }
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
