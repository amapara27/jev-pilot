import JevPilotCore
import SwiftUI

struct TimelineView: View {
  let events: [DebugEvent]

  var body: some View {
    if events.isEmpty {
      ContentUnavailableView(
        "No run yet",
        systemImage: "waveform",
        description: Text("Speak or type a command to inspect every observe → decide → act step.")
      )
    } else {
      List(events.reversed()) { event in
        VStack(alignment: .leading, spacing: 5) {
          HStack {
            Text(event.kind.rawValue.uppercased())
              .font(.caption2.monospaced().weight(.bold))
              .foregroundStyle(color(for: event.kind))
            Text(event.title).font(.headline)
            Spacer()
            Text(event.timestamp, style: .time).font(.caption).foregroundStyle(.secondary)
          }
          Text(event.detail)
            .font(.callout.monospaced())
            .textSelection(.enabled)
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
      }
    }
  }

  private func color(for kind: DebugEvent.Kind) -> Color {
    switch kind {
    case .observation: .blue
    case .candidates: .purple
    case .decision: .orange
    case .safety: .pink
    case .execution: .green
    case .error: .red
    }
  }
}

struct JSONDebugView<Value: Encodable>: View {
  let title: String
  let value: Value?

  var body: some View {
    ScrollView {
      Text(rendered)
        .font(.callout.monospaced())
        .textSelection(.enabled)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
    }
  }

  private var rendered: String {
    guard let value else { return "No \(title.lowercased()) captured yet." }
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    encoder.dateEncodingStrategy = .iso8601
    guard let data = try? encoder.encode(value) else {
      return "Could not encode \(title.lowercased())."
    }
    return String(data: data, encoding: .utf8) ?? ""
  }
}

struct ActionsView: View {
  let actions: [ActionCandidate]
  let decision: ActionDecision?

  var body: some View {
    if actions.isEmpty {
      ContentUnavailableView("No actions", systemImage: "cursorarrow.slash")
    } else {
      List(actions) { candidate in
        HStack(alignment: .top) {
          VStack(alignment: .leading, spacing: 4) {
            Text(candidate.action.summary).font(.headline)
            Text(candidate.id).font(.caption.monospaced()).foregroundStyle(.secondary)
            Text(candidate.criterion).font(.callout).foregroundStyle(.secondary)
          }
          Spacer()
          if let probability = decision?.probabilities[candidate.id] {
            Text(probability, format: .percent.precision(.fractionLength(1)))
              .monospacedDigit()
          }
          if decision?.candidate.id == candidate.id {
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
          }
        }
        .padding(.vertical, 3)
      }
    }
  }
}
