// Flat run rows and a compact usage ledger carry the same visual language as Control.
import JevPilotCore
import SwiftUI

struct HistoryView: View {
  @EnvironmentObject private var store: RunStore
  @State private var search = ""
  @State private var selectedID: UUID?
  @State private var clearConfirmation = false
  private var filtered: [RunRecord] {
    store.records.filter { search.isEmpty || $0.command.localizedCaseInsensitiveContains(search) || ($0.outcome?.rawValue.localizedCaseInsensitiveContains(search) ?? false) }
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      HStack(alignment: .top) {
        VStack(alignment: .leading, spacing: 7) {
          Text("History").font(PilotTheme.display(34)).tracking(-0.8)
        }
        Spacer()
        Button("Clear history", role: .destructive) { clearConfirmation = true }
          .buttonStyle(PilotButtonStyle()).disabled(!store.records.contains { $0.outcome != nil })
      }.padding(28)
      HStack(spacing: 10) {
        Image(systemName: "magnifyingglass").foregroundStyle(PilotTheme.muted).accessibilityHidden(true)
        TextField("Search commands or outcomes", text: $search).font(PilotTheme.label(12)).textFieldStyle(.plain).accessibilityLabel("Search runs")
      }.padding(13).background(PilotTheme.surface, in: RoundedRectangle(cornerRadius: 10)).overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(PilotTheme.line))
        .padding(.horizontal, 28).padding(.bottom, 24)
      PilotRule()
      if filtered.isEmpty {
        VStack(alignment: .leading, spacing: 10) {
          SectionCaption(title: search.isEmpty ? "No saved runs" : "No matches")
          Spacer()
        }.padding(28).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
      } else {
        HStack(spacing: 0) {
          ScrollView {
            LazyVStack(spacing: 0) {
              ForEach(filtered) { run in
                Button { selectedID = run.id } label: {
                  VStack(alignment: .leading, spacing: 11) {
                    OutcomeLabel(outcome: run.outcome)
                    Text(run.command).font(.system(size: 13, weight: .medium)).lineLimit(3).multilineTextAlignment(.leading)
                    Text(run.startedAt.formatted(date: .abbreviated, time: .shortened)).font(PilotTheme.mono(9)).foregroundStyle(PilotTheme.muted)
                    Text("\(run.actionCount) actions  /  \(durationLabel(run.duration))").font(PilotTheme.mono(9)).foregroundStyle(PilotTheme.muted)
                  }.frame(maxWidth: .infinity, alignment: .leading).padding(18)
                    .background(selectedID == run.id ? PilotTheme.inset : .clear)
                    .overlay(alignment: .leading) { if selectedID == run.id { Rectangle().fill(PilotTheme.accent).frame(width: 2) } }
                    .contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityAddTraits(selectedID == run.id ? .isSelected : [])
                PilotRule()
              }
            }
          }.frame(width: 215)
          Rectangle().fill(PilotTheme.line).frame(width: 1)
          if let run = filtered.first(where: { $0.id == selectedID }) {
            ScrollView {
              VStack(alignment: .leading, spacing: 22) {
                HStack {
                  OutcomeLabel(outcome: run.outcome)
                  Spacer()
                  Button(role: .destructive) { store.delete(run.id); selectedID = filtered.first?.id } label: { Image(systemName: "trash") }
                    .buttonStyle(PilotButtonStyle()).disabled(run.outcome == nil).help("Delete run").accessibilityLabel("Delete run")
                }
                Text(run.command).font(PilotTheme.display(26)).tracking(-0.5).textSelection(.enabled)
                Text(run.startedAt.formatted(date: .abbreviated, time: .shortened)).font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
                HStack {
                  MetricView(title: "Duration", value: durationLabel(run.duration))
                  MetricView(title: "Actions", value: "\(run.actionCount)")
                }
                HStack {
                  MetricView(title: "Requests", value: "\(run.requests.count)")
                  MetricView(title: "Est. cost", value: run.hasIncompleteUsage && run.estimatedCost == 0 ? "—" : costLabel(run.estimatedCost))
                }
                if run.hasIncompleteUsage { Text("Some usage unavailable").font(.caption).foregroundStyle(PilotTheme.muted) }
                if let timings = run.timings, !timings.isEmpty {
                  PilotRule()
                  SectionCaption(title: "Timing")
                  ForEach(Array(timings.enumerated()), id: \.offset) { _, timing in
                    HStack {
                      Text(timing.step == 0 ? timing.stage : "\(timing.stage) · step \(timing.step)")
                      Spacer()
                      Text("\(timing.milliseconds) ms").monospacedDigit()
                    }.font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
                  }
                }
                PilotRule()
                SectionCaption(title: "Action trail")
                RunEventList(events: run.events)
              }.padding(24)
            }.frame(maxWidth: .infinity, maxHeight: .infinity)
          } else {
            VStack(alignment: .leading, spacing: 8) {
              Text("Select a run.").font(PilotTheme.display(26))
              Spacer()
            }.padding(28).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
          }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
      }
    }.foregroundStyle(PilotTheme.text).background(PilotTheme.background)
      .onAppear { selectedID = selectedID ?? filtered.first?.id }
      .alert("Clear saved history?", isPresented: $clearConfirmation) {
        Button("Cancel", role: .cancel) {}
        Button("Clear history", role: .destructive) { store.clear(); selectedID = nil }
      } message: { Text("Completed runs and their local usage totals will be removed. An active run will be kept.") }
  }
}

/// Date-scoped usage reads as a ledger, with cost and latency taking visual priority.
struct UsageView: View {
  @EnvironmentObject private var store: RunStore
  @State private var period = 0
  private var records: [RunRecord] {
    let calendar = Calendar.current
    let start = calendar.startOfDay(for: .now)
    let cutoff = calendar.date(byAdding: .day, value: period == 0 ? 0 : period == 1 ? -6 : -29, to: start)!
    return store.records.filter { $0.startedAt >= cutoff }
  }
  var body: some View {
    let summary = UsageSummary(records: records)
    ScrollView {
      VStack(alignment: .leading, spacing: 28) {
        HStack(alignment: .top) {
          VStack(alignment: .leading, spacing: 7) {
            Text("Usage").font(PilotTheme.display(34)).tracking(-0.8)
          }
          Spacer()
        }
        PilotChoiceGroup(label: "Usage period", selection: $period, choices: [
          .init(value: 0, title: "Today"), .init(value: 1, title: "7 days"), .init(value: 2, title: "30 days")
        ]).frame(maxWidth: 320)
        Surface {
          VStack(alignment: .leading, spacing: 26) {
            SectionCaption(title: "Estimated API cost", trailing: "USD")
            Text(summary.incomplete && summary.estimatedCost == 0 ? "Unavailable" : costLabel(summary.estimatedCost))
              .font(PilotTheme.label(43, weight: .regular)).tracking(-2).monospacedDigit()
              .foregroundStyle(PilotTheme.accent).minimumScaleFactor(0.65).lineLimit(1)
            PilotRule()
            HStack {
              MetricView(title: "Input tokens", value: summary.incomplete && summary.inputTokens == 0 ? "—" : summary.inputTokens.formatted())
              MetricView(title: "Output tokens", value: summary.incomplete && summary.outputTokens == 0 ? "—" : summary.outputTokens.formatted())
            }
            if summary.incomplete {
              Text("Partial total · some usage unavailable").font(.system(size: 11)).foregroundStyle(PilotTheme.muted)
            }
          }
        }
        SectionCaption(title: "Activity & performance")
        PilotRule()
        HStack {
          MetricView(title: "Runs", value: "\(summary.runs)")
          MetricView(title: "Actions", value: "\(summary.actions)")
          MetricView(title: "API requests", value: "\(summary.requests)")
        }
        PilotRule()
        HStack {
          MetricView(title: "Run time", value: durationLabel(summary.duration))
          MetricView(title: "Avg. request latency", value: summary.averageLatency.map { "\(Int($0)) ms" } ?? "—")
        }
        Text("Based on retained runs on this Mac.")
          .font(.system(size: 11)).lineSpacing(5).foregroundStyle(PilotTheme.muted).padding(.top, 6)
          .help("Includes up to 100 retained runs. Deleting history removes their totals. Estimates may differ from your provider’s invoice.")
      }.padding(30).frame(maxWidth: 980).frame(maxWidth: .infinity)
    }.foregroundStyle(PilotTheme.text).background(PilotTheme.background)
  }
}
