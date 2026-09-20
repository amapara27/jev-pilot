// Provides the settings form for managing the TypeSafe API key.
import JevPilotCore
import SwiftUI

/// Lets the user save or remove the key from macOS Keychain.
struct SettingsView: View {
  @State private var apiKey = ""
  @State private var message = ""
  private let keyStore = KeychainAPIKeyStore()

  var body: some View {
    Form {
      Section("TypeSafe / Jev") {
        SecureField("API key", text: $apiKey)
          .textFieldStyle(.roundedBorder)
        HStack {
          Button("Save to Keychain", action: save)
            .buttonStyle(.borderedProminent)
          Button("Remove", role: .destructive, action: remove)
          Spacer()
        }
        Text(
          "Developers can alternatively set TYPESAFE_API_KEY. Keys are sent directly to TypeSafe and are never written to logs."
        )
        .font(.caption)
        .foregroundStyle(.secondary)
        if !message.isEmpty { Text(message).font(.caption) }
      }
    }
    .formStyle(.grouped)
    .padding()
    .frame(width: 520, height: 260)
    .onAppear {
      if (try? keyStore.load()) != nil { message = "An API key is stored in Keychain." }
    }
  }

  /// Saves the trimmed key and clears it from the visible form.
  private func save() {
    do {
      try keyStore.save(apiKey.trimmingCharacters(in: .whitespacesAndNewlines))
      apiKey = ""
      message = "Saved securely in Keychain."
    } catch {
      message = error.localizedDescription
    }
  }

  /// Removes the saved key and reports the result.
  private func remove() {
    do {
      try keyStore.delete()
      apiKey = ""
      message = "Removed from Keychain."
    } catch {
      message = error.localizedDescription
    }
  }
}
