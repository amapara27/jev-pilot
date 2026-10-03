// Separates supported command clauses while keeping dictated text literal.
import Foundation

/// Tracks the final operation for each clause; prerequisites remain Jev-selected.
struct CommandInstruction {
  let text: String

  var completionKinds: Set<ActionKind> {
    let lower = text.lowercased()
    if lower.hasPrefix("type ") || lower.hasPrefix("write ") || lower.hasPrefix("enter ") || lower.hasPrefix("dictate ") {
      return [.typeText, .terminalType]
    }
    if lower.hasPrefix("run ") || lower.hasPrefix("terminal command ") { return [.terminalRun] }
    if lower.range(of: #"^(create|make|start|open) (a )?(new )?note\b"#, options: .regularExpression) != nil {
      return [.notesCreateNote]
    }
    if ["locate ", "find ", "search "].contains(where: lower.hasPrefix) { return [.searchInApp] }
    if ["open folder ", "go to folder ", "go to "].contains(where: lower.hasPrefix) { return [.finderOpenFolder] }
    if lower.hasPrefix("rename ") || lower.hasPrefix("call it ") { return [.finderRenameItem] }
    if lower.hasPrefix("copy ") { return [.finderCopyItem] }
    if lower.hasPrefix("move ") { return [.finderMoveItem] }
    if lower.hasPrefix("select ") { return [.finderSelectItem, .selectTab] }
    if lower.hasPrefix("click ") { return [.clickElement, .activateMenu] }
    if lower.contains("exit fullscreen") || lower.contains("exit full screen") { return [.exitFullScreen] }
    if lower.contains("fullscreen") || lower.contains("full screen") { return [.enterFullScreen] }
    if lower.hasPrefix("minimize") { return [.minimizeWindow] }
    if lower.hasPrefix("restore") { return [.restoreWindow] }
    if lower.hasPrefix("close") { return [.closeWindow] }
    if lower.contains("next tab") { return [.nextTab] }
    if lower.contains("previous tab") { return [.previousTab] }
    if lower.contains("back") { return [.navigateBack] }
    if lower.contains("forward") { return [.navigateForward] }
    if lower.hasPrefix("scroll up") { return [.scrollUp] }
    if lower.hasPrefix("scroll down") { return [.scrollDown] }
    if lower.hasPrefix("press ") { return [.pressKey] }
    if ["open ", "focus ", "launch ", "switch to ", "use "].contains(where: lower.hasPrefix) {
      return [.openApp, .focusApp, .finderOpenItem, .finderOpenFolder]
    }
    return []
  }

  /// Split before action verbs, never inside a typing payload (including `and`).
  static func parse(_ goal: String) -> [Self] {
    let typing = try! NSRegularExpression(pattern: #"(?i)(?:^|[,;.]\s*|\b(?:and then|then|and|after that)\s+)(?:please\s+)?(type|write|enter|dictate)\s+"#)
    let match = typing.firstMatch(in: goal, range: NSRange(goal.startIndex..., in: goal))
    var command = goal
    var payload: String?
    if let match, let verb = Range(match.range(at: 1), in: goal), let entire = Range(match.range, in: goal) {
      command = String(goal[..<entire.lowerBound])
      payload = String(goal[verb.lowerBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
    }
    // Only command-side transitions are split. Unknown phrases stay intact and can pause.
    let boundary = try! NSRegularExpression(pattern: #"(?i)\s*(?:[,;.]|\b(?:and then|then|and|after that))\s+(?=(?:open|launch|focus|use|switch|create|make|start|locate|find|search|select|click|rename|copy|move|go|run|minimize|restore|close|enter|exit|scroll|press)\b)|\s+(?=(?:create|make|start)\s+(?:a\s+)?(?:new\s+)?note\b)"#)
    var clauses: [String] = []
    var start = command.startIndex
    for split in boundary.matches(in: command, range: NSRange(command.startIndex..., in: command)) {
      guard let range = Range(split.range, in: command) else { continue }
      clauses.append(String(command[start..<range.lowerBound]))
      start = range.upperBound
    }
    clauses.append(String(command[start...]))
    clauses = clauses.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
      .filter { !$0.isEmpty }
      .map { $0.lowercased().hasPrefix("please ") ? String($0.dropFirst(7)) : $0 }
    if var payload {
      // ponytail: narrow English suffix; quotation marks protect an intentional literal suffix.
      let suffix = " in a new note"
      let literal = payload.drop(while: { !$0.isWhitespace }).trimmingCharacters(in: .whitespaces)
      if !literal.hasPrefix("\"") && !literal.hasPrefix("“"), payload.lowercased().hasSuffix(suffix) {
        payload = String(payload.dropLast(suffix.count))
        if !clauses.contains(where: { Self(text: $0).completionKinds == [.notesCreateNote] }) {
          clauses.append("create a new note")
        }
      }
      clauses.append(payload)
    }
    return clauses.map(Self.init(text:))
  }
}
