// Checks exact text insertion, caret replacement, and Unicode batching without native input.
import XCTest
@testable import JevPilotCore

final class TextInputTests: XCTestCase {
  private func field(_ value: String, selection: TextSelection? = nil) -> UIElementState {
    .init(id: "editor", role: "AXTextArea", label: "Body", value: value, isFocused: true, textSelection: selection)
  }

  func testCaretAndSelectionReplacementPreserveExistingText() {
    let old = field("Hello old world", selection: .init(location: 6, length: 3))
    XCTAssertEqual(TextInput.expectedValue(before: old, inserting: "new"), "Hello new world")
    XCTAssertTrue(TextInput.insertionMatches(before: old, after: field("Hello new world"), text: "new"))
    XCTAssertFalse(TextInput.insertionMatches(before: old, after: field("new"), text: "new"))
    XCTAssertFalse(TextInput.insertionMatches(before: old, after: field("Hello old world new"), text: "new"))
    let identical = field("same", selection: .init(location: 0, length: 4))
    XCTAssertFalse(TextInput.insertionMatches(before: identical, after: identical, text: "same"))
    XCTAssertTrue(TextInput.insertionMatches(before: identical,
      after: field("same", selection: .init(location: 4, length: 0)), text: "same"))
  }

  func testMissingCaretRequiresExactInsertionNotAnExistingSubstring() {
    XCTAssertTrue(TextInput.insertionMatches(before: field("Hello world"), after: field("Hello new world"), text: "new "))
    XCTAssertFalse(TextInput.insertionMatches(before: field("hello"), after: field("hello!"), text: "hello"))
    XCTAssertFalse(TextInput.insertionMatches(before: field("hello"), after: field("hellohellohello"), text: "hello"))
    XCTAssertFalse(TextInput.insertionMatches(before: field("hello"), after: field("hello"), text: "hello"))
    XCTAssertTrue(TextInput.insertionMatches(before: field("ab"), after: field("abab"), text: "ba"))
    XCTAssertTrue(TextInput.insertionMatches(before: field("aa"), after: field("aaaa"), text: "aa"))
  }

  func testLongTextAndUTF16SelectionAreVerifiedWithoutProviderTruncation() {
    let prefix = String(repeating: "x", count: 400)
    let old = field(prefix + "🙂end", selection: .init(location: 402, length: 0))
    XCTAssertEqual(TextInput.expectedValue(before: old, inserting: "héllo"), prefix + "🙂hélloend")
    XCTAssertTrue(TextInput.insertionMatches(before: old, after: field(prefix + "🙂hélloend"), text: "héllo"))
    for selection in [TextSelection(location: -1, length: 0), .init(location: 500, length: 0), .init(location: 2, length: Int.max)] {
      XCTAssertNil(TextInput.expectedValue(before: field("abc", selection: selection), inserting: "hello"))
    }
    let truncated = UIElementState(id: "editor", role: "AXTextArea", value: "x", valueIsTruncated: true)
    XCTAssertFalse(TextInput.insertionMatches(before: truncated, after: field("xy"), text: "y"))
  }

  func testUnicodeBatchesNeverSplitSurrogatePairs() {
    let text = String(repeating: "a", count: 19) + "🙂 café 日本語 👨‍👩‍👧‍👦"
    let chunks = TextInput.unicodeChunks(text)
    XCTAssertEqual(chunks.flatMap { $0 }, Array(text.utf16))
    XCTAssertEqual(chunks.map { String(decoding: $0, as: UTF16.self) }.joined(), text)
    XCTAssertTrue(chunks.allSatisfy { !$0.isEmpty && $0.count <= 20 })
    XCTAssertTrue(TextInput.unicodeChunks("").isEmpty)
  }
}
