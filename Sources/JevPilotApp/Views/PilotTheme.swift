// Shared visual language: neutral greys, cream, restrained gold, and quiet type.
import AppKit
import SwiftUI

/// Semantic colors follow system appearance while keeping a distinct product identity.
enum PilotTheme {
  static let background = adaptive(0xF4F2ED, 0x1B1B1B)
  static let surface = adaptive(0xFCFBF8, 0x232323)
  static let inset = adaptive(0xE9E7E2, 0x2D2C2A)
  static let text = adaptive(0x282828, 0xF3F0E9)
  static let muted = adaptive(0x67645F, 0xB2AEA6)
  static let line = adaptive(0xDAD7D0, 0x3C3B37)
  static let accent = adaptive(0x80632F, 0xD8BE85)
  static let buttonFill = adaptive(0xD9C28F, 0xCAB17B)
  static let onAccent = adaptive(0x26231D, 0x26231D)
  static let rail = Color(white: 0.12)
  static let railText = Color(red: 0.94, green: 0.93, blue: 0.90)
  static let railMuted = Color(white: 0.66)
  static let signal = Color(red: 0.85, green: 0.75, blue: 0.52)
  static let danger = adaptive(0xA93328, 0xF6A398)
  static let onDanger = adaptive(0xFFFFFF, 0x291A18)
  static let rule: CGFloat = 1

  /// A restrained serif display face contrasts with compact sans-serif controls.
  static func display(_ size: CGFloat) -> Font {
    .system(size: size, weight: .regular, design: .serif)
  }
  static func label(_ size: CGFloat = 13, weight: Font.Weight = .medium) -> Font {
    .system(size: size, weight: weight, design: .rounded)
  }

  static func mono(_ size: CGFloat = 11, weight: Font.Weight = .medium) -> Font {
    .system(size: size, weight: weight, design: .monospaced)
  }
  private static func adaptive(_ light: UInt, _ dark: UInt) -> Color {
    Color(nsColor: NSColor(name: nil) { appearance in
      let rgb = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
      return NSColor(srgbRed: Double((rgb >> 16) & 255) / 255, green: Double((rgb >> 8) & 255) / 255, blue: Double(rgb & 255) / 255, alpha: 1)
    })
  }
}

/// Static brand mark; it never pretends to be an audio meter.
struct PilotMark: View {
  var size: CGFloat = 28
  var body: some View {
    HStack(alignment: .center, spacing: size * 0.12) {
      ForEach(Array([0.35, 0.75, 1.0, 0.55].enumerated()), id: \.offset) { _, value in
        RoundedRectangle(cornerRadius: 1).frame(width: size * 0.14, height: size * value)
      }
    }.frame(width: size, height: size).accessibilityHidden(true)
  }
}

/// Custom button geometry retains native Button keyboard and accessibility semantics.
struct PilotButtonStyle: ButtonStyle {
  var prominent = false
  var destructive = false
  func makeBody(configuration: Configuration) -> some View {
    Face(configuration: configuration, prominent: prominent, destructive: destructive)
  }
  private struct Face: View {
    let configuration: ButtonStyle.Configuration
    let prominent: Bool
    let destructive: Bool
    @Environment(\.isEnabled) private var enabled
    @Environment(\.isFocused) private var focused
    @State private var hovered = false
    private var fill: Color {
      if prominent { return destructive ? PilotTheme.danger : PilotTheme.buttonFill }
      return hovered ? PilotTheme.inset : PilotTheme.surface
    }
    var body: some View {
      configuration.label.font(PilotTheme.label(12, weight: .semibold))
        .padding(.horizontal, 14).padding(.vertical, 11)
        .foregroundStyle(prominent ? (destructive ? PilotTheme.onDanger : PilotTheme.onAccent) : destructive ? PilotTheme.danger : PilotTheme.text)
        .background(fill.opacity(configuration.isPressed ? 0.75 : 1), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(focused ? PilotTheme.accent : prominent ? .clear : PilotTheme.line, lineWidth: focused ? 2 : 1))
        .opacity(enabled ? 1 : 0.4)
        .contentShape(Rectangle())
        .onHover { hovered = $0 }
    }
  }
}

/// Small labels and hairlines organize the interface without nested card chrome.
struct SectionCaption: View {
  let title: String
  var trailing = ""
  var body: some View {
    HStack {
      Text(title.uppercased()).tracking(1.3)
      Spacer()
      if !trailing.isEmpty { Text(trailing).tracking(0.5) }
    }.font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
  }
}
struct PilotRule: View {
  var body: some View { Rectangle().fill(PilotTheme.line).frame(height: 1).accessibilityHidden(true) }
}
