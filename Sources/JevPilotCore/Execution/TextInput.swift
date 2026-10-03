// Shares exact insertion checks and Unicode event batching across text operations.
import Foundation

enum TextInput {
  static let maximumValueLength = 65_536

  /// Computes replacement at the observed caret/selection without replacing the whole document.
  static func expectedValue(before: UIElementState, inserting text: String) -> String? {
    guard before.valueIsTruncated != true, let value = before.value,
      let selection = before.textSelection,
      selection.location >= 0, selection.length >= 0,
      selection.location <= value.utf16.count,
      selection.length <= value.utf16.count - selection.location,
      let range = Range(NSRange(location: selection.location, length: selection.length), in: value)
    else { return nil }
    return value.replacingCharacters(in: range, with: text)
  }

  /// Missing caret metadata permits only an exact, single insertion delta, not substring presence.
  static func insertionMatches(before: UIElementState, after: UIElementState, text: String) -> Bool {
    guard before.valueIsTruncated != true, after.valueIsTruncated != true,
      before.isTextInput, after.isTextInput, before.role == after.role,
      before.subrole == after.subrole, before.label == after.label,
      let old = before.value, let new = after.value, !text.isEmpty
    else { return false }
    if before.textSelection != nil {
      guard expectedValue(before: before, inserting: text) == new else { return false }
      if old != new { return true }
      // Replacing identical selected text must still collapse the selection at the new caret.
      return after.textSelection == TextSelection(location: before.textSelection!.location + text.utf16.count, length: 0)
        && before.textSelection != after.textSelection
    }
    guard new.utf16.count == old.utf16.count + text.utf16.count else { return false }
    let oldUnits = Array(old.utf16), newUnits = Array(new.utf16)
    let prefix = zip(oldUnits, newUnits).prefix(while: { $0 == $1 }).count
    let suffix = zip(oldUnits.reversed(), newUnits.reversed()).prefix(while: { $0 == $1 }).count
    let firstPossibleOffset = oldUnits.count - suffix
    guard firstPossibleOffset <= prefix else { return false }
    // Overlapping repeated text can hide the actual insertion point within the matching prefix.
    let range = NSRange(location: firstPossibleOffset, length: prefix - firstPossibleOffset + text.utf16.count)
    return (new as NSString).range(of: text, options: .literal, range: range).location != NSNotFound
  }

  /// Keep surrogate pairs together; emit bounded batches without clipboard side effects.
  static func unicodeChunks(_ text: String) -> [[UniChar]] {
    var chunks: [[UniChar]] = []
    var chunk: [UniChar] = []
    for scalar in text.unicodeScalars {
      let units = Array(String(scalar).utf16)
      if chunk.count + units.count > 20 { chunks.append(chunk); chunk = [] }
      chunk.append(contentsOf: units)
    }
    if !chunk.isEmpty { chunks.append(chunk) }
    return chunks
  }
}
