// App-owned choice and toggle styling keeps preferences consistent with the workspace.
import SwiftUI

/// Small choice sets stay visible instead of opening a system popup menu.
struct PilotChoice<Value: Hashable>: Identifiable {
  let value: Value
  let title: String
  var detail: String = ""
  var id: Value { value }
}

/// Buttons preserve keyboard activation and announce selection without relying on color.
struct PilotChoiceGroup<Value: Hashable>: View {
  let label: String
  @Binding var selection: Value
  let choices: [PilotChoice<Value>]

  var body: some View {
    HStack(spacing: 4) {
      ForEach(choices) { choice in
        Button { selection = choice.value } label: {
          VStack(spacing: 5) {
            HStack(spacing: 5) {
              Image(systemName: "checkmark").font(.system(size: 8, weight: .bold))
                .opacity(selection == choice.value ? 1 : 0).accessibilityHidden(true)
              Text(choice.title).font(PilotTheme.label(12, weight: .semibold))
              // Balance the checkmark's width so every label remains centered.
              Color.clear.frame(width: 8, height: 1).accessibilityHidden(true)
            }
            if !choice.detail.isEmpty {
              Text(choice.detail).font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
            }
          }.frame(maxWidth: .infinity).padding(.vertical, 10).contentShape(Rectangle())
        }
        .buttonStyle(ChoiceButtonStyle(selected: selection == choice.value))
        .accessibilityLabel(choice.detail.isEmpty ? choice.title : "\(choice.title), \(choice.detail)")
        .accessibilityAddTraits(selection == choice.value ? .isSelected : [])
      }
    }.padding(4)
      .background(PilotTheme.inset, in: RoundedRectangle(cornerRadius: 12))
      .accessibilityElement(children: .contain).accessibilityLabel(label)
  }
}

/// Selection, pointer, keyboard focus, and disabled states share the same geometry.
private struct ChoiceButtonStyle: ButtonStyle {
  let selected: Bool
  func makeBody(configuration: Configuration) -> some View {
    Face(configuration: configuration, selected: selected)
  }
  private struct Face: View {
    let configuration: ButtonStyle.Configuration
    let selected: Bool
    @Environment(\.isEnabled) private var enabled
    @Environment(\.isFocused) private var focused
    @State private var hovered = false
    var body: some View {
      configuration.label
        .foregroundStyle(selected ? PilotTheme.accent : PilotTheme.muted)
        .background(selected || hovered ? PilotTheme.surface : .clear, in: RoundedRectangle(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(focused ? PilotTheme.accent : selected ? PilotTheme.line : .clear, lineWidth: focused ? 2 : 1))
        .opacity(enabled ? (configuration.isPressed ? 0.65 : 1) : 0.4)
        .onHover { hovered = $0 }
    }
  }
}

/// Native Toggle semantics with a compact, static gold switch and a visible on mark.
struct PilotToggleStyle: ToggleStyle {
  func makeBody(configuration: Configuration) -> some View {
    HStack {
      configuration.label.font(PilotTheme.label())
      Spacer()
      Button { configuration.isOn.toggle() } label: {
        Capsule().fill(configuration.isOn ? PilotTheme.buttonFill : PilotTheme.inset)
          .overlay(Capsule().strokeBorder(PilotTheme.line))
          .overlay(alignment: configuration.isOn ? .trailing : .leading) {
            Circle().fill(configuration.isOn ? PilotTheme.onAccent : PilotTheme.muted)
              .overlay {
                if configuration.isOn {
                  Image(systemName: "checkmark").font(.system(size: 8, weight: .bold)).foregroundStyle(PilotTheme.buttonFill)
                }
              }.padding(4).frame(width: 26, height: 26)
          }.frame(width: 44, height: 26).padding(.vertical, 4)
      }.buttonStyle(.plain)
        .accessibilityLabel(configuration.isOn ? "On" : "Off")
    }.accessibilityElement(children: .combine)
  }
}
