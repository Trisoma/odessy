# Odessy desktop

Run `flutter pub get`, then `flutter run -d windows` (`linux` or `macos` on those platforms). The application starts disconnected until a service is configured. The checkout-specific Windows build can start its owned local service; see the runbook.

See [the runbook](../../docs/ui/README.md) for local service setup, test commands and feature boundaries. Windows native builds are verified; macOS and Linux runners require platform qualification.

UI: `lib/features/*/presentation/`. Protected foundation: `lib/app/` and `../../packages/odessy_client/`. Presentation uses the controller rather than HTTP clients, provider SDKs or native execution.
