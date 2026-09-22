// Computes transparent word-error metrics for a known reference phrase.
import Foundation

public struct TranscriptionAccuracy: Equatable, Sendable {
  public let substitutions: Int
  public let insertions: Int
  public let deletions: Int
  public let referenceWordCount: Int
  public let wordErrorRate: Double

  public init(
    substitutions: Int,
    insertions: Int,
    deletions: Int,
    referenceWordCount: Int
  ) {
    self.substitutions = substitutions
    self.insertions = insertions
    self.deletions = deletions
    self.referenceWordCount = referenceWordCount
    let errors = substitutions + insertions + deletions
    wordErrorRate = referenceWordCount == 0 ? (errors == 0 ? 0 : 1) : Double(errors) / Double(referenceWordCount)
  }

  /// Lowercases and removes punctuation before a Levenshtein alignment with operation counts.
  public static func compare(reference: String, transcript: String) -> Self {
    let expected = words(in: reference)
    let actual = words(in: transcript)
    struct Score {
      var edits: Int
      var substitutions: Int
      var insertions: Int
      var deletions: Int
    }
    var table = Array(
      repeating: Array(repeating: Score(edits: 0, substitutions: 0, insertions: 0, deletions: 0), count: actual.count + 1),
      count: expected.count + 1
    )
    if !expected.isEmpty {
      for row in 1...expected.count {
        table[row][0] = Score(edits: row, substitutions: 0, insertions: 0, deletions: row)
      }
    }
    if !actual.isEmpty {
      for column in 1...actual.count {
        table[0][column] = Score(edits: column, substitutions: 0, insertions: column, deletions: 0)
      }
    }
    if !expected.isEmpty, !actual.isEmpty {
      for row in 1...expected.count {
        for column in 1...actual.count {
          if expected[row - 1] == actual[column - 1] {
            table[row][column] = table[row - 1][column - 1]
          } else {
            let priorSubstitution = table[row - 1][column - 1]
            let priorInsertion = table[row][column - 1]
            let priorDeletion = table[row - 1][column]
            let candidates = [
              Score(edits: priorSubstitution.edits + 1, substitutions: priorSubstitution.substitutions + 1, insertions: priorSubstitution.insertions, deletions: priorSubstitution.deletions),
              Score(edits: priorInsertion.edits + 1, substitutions: priorInsertion.substitutions, insertions: priorInsertion.insertions + 1, deletions: priorInsertion.deletions),
              Score(edits: priorDeletion.edits + 1, substitutions: priorDeletion.substitutions, insertions: priorDeletion.insertions, deletions: priorDeletion.deletions + 1),
            ]
            table[row][column] = candidates.min { lhs, rhs in
              (lhs.edits, lhs.substitutions, lhs.insertions, lhs.deletions)
                < (rhs.edits, rhs.substitutions, rhs.insertions, rhs.deletions)
            }!
          }
        }
      }
    }
    let score = table[expected.count][actual.count]
    return Self(
      substitutions: score.substitutions,
      insertions: score.insertions,
      deletions: score.deletions,
      referenceWordCount: expected.count
    )
  }

  public static func normalized(_ text: String) -> String {
    words(in: text).joined(separator: " ")
  }

  private static func words(in text: String) -> [String] {
    text.lowercased()
      .components(separatedBy: CharacterSet.alphanumerics.inverted)
      .filter { !$0.isEmpty }
  }
}
