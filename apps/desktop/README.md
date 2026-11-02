# Odessy desktop preview

Run `flutter pub get`, then `flutter run -d windows` (`linux` or `macos` on those platforms). Starts in explicit demo mode; no model or tool executes.

See [the runbook](../../docs/ui/README.md) for live local API connection, test commands, feature boundaries and teammate extension instructions. Windows native builds are verified; macOS/Linux runners require platform qualification.

UI: `lib/features/*/presentation/`. Protected foundation: `lib/app/` and `../../packages/odessy_client/`. Presentation uses the controller rather than HTTP clients, provider SDKs or native execution.
