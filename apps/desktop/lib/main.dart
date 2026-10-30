import 'package:flutter/material.dart';
import 'dart:io' show Platform;

import 'app/app.dart';
import 'app/setup/installed_startup.dart';
import 'app/setup/instance_guard.dart';
import 'app/setup/native_picker.dart';
import 'app/setup/local_desktop_application.dart';
import 'app/setup/setup_application.dart';
import 'app/theme.dart';

Future<void> main(List<String> arguments) async {
  WidgetsFlutterBinding.ensureInitialized();
  final mode = installationMode(arguments);
  if (mode == null && const bool.fromEnvironment('ODESSY_LOCAL_DESKTOP')) {
    runApp(const LocalDesktopApplication());
    return;
  }
  if (mode == null) {
    runApp(applicationFor(arguments));
    return;
  }
  // Windows activates the matching native window before creating Flutter.
  if (Platform.isWindows) {
    runApp(applicationFor(arguments));
    return;
  }
  // One window per mode and location. The lock is advisory, so a crashed process
  // never leaves a stale lock behind. Focusing the existing window is a native
  // runner concern; until then the second launch says so instead of opening a
  // second installer over the first.
  final key = launchArgument(arguments, '--install-root');
  final guard =
      await InstanceGuard.acquire(key.isEmpty ? mode : key, mode: mode);
  if (guard == null) {
    runApp(_SecondInstance(
        arguments: arguments, mode: mode, onContinue: applicationFor));
    return;
  }
  runApp(applicationFor(arguments));
}

/// The surface for the parsed arguments. Kept apart from [main] so the native
/// picker wiring is covered by a test and not only by the packaged build.
Widget applicationFor(
  List<String> arguments, {
  NativePicker picker = const FileSelectorPicker(),
}) {
  final mode = installationMode(arguments);
  final installRoot = launchArgument(arguments, '--install-root');
  final payload = launchArgument(arguments, '--payload');
  final launchId = launchArgument(arguments, '--launch-id');
  if (mode == 'installed') {
    return InstalledApplication(
        installRoot: installRoot, launchId: launchId, picker: picker);
  }
  return mode == null
      ? const OdessyApp()
      : SetupApplication(
          mode: mode,
          payloadRoot: payload,
          installRoot: installRoot,
          picker: picker);
}

/// Shown when another setup or installed window already holds the lock. Both
/// actions are explicit: nothing repeats automatically, and closing here leaves
/// the other window untouched.
class _SecondInstance extends StatelessWidget {
  const _SecondInstance({
    required this.arguments,
    required this.mode,
    required this.onContinue,
  });

  final List<String> arguments;
  final String mode;
  final Widget Function(List<String>) onContinue;

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Odessy setup',
        theme: odessyTheme(Brightness.dark),
        home: Scaffold(
          body: SafeArea(
            child: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 460),
                child: Padding(
                  padding: const EdgeInsets.all(28),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Odessy is already open',
                          style: Theme.of(context).textTheme.headlineSmall),
                      const SizedBox(height: 12),
                      Text(
                        mode == 'installed'
                            ? 'Another Odessy window is using this installation. Close or use that window.'
                            : 'Setup is already running in another window. Closing this one changes nothing.',
                        style: Theme.of(context).textTheme.bodyMedium,
                      ),
                      const SizedBox(height: 20),
                      OverflowBar(
                        spacing: 12,
                        children: [
                          FilledButton(
                            onPressed: () => exitApp(),
                            child: const Text('Close'),
                          ),
                          TextButton(
                            onPressed: () => runApp(onContinue(arguments)),
                            child: const Text('Open another window anyway'),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      );
}

String launchArgument(List<String> arguments, String name) {
  final index = arguments.indexOf(name);
  if (index >= 0 && index + 1 < arguments.length) return arguments[index + 1];
  for (final argument in arguments) {
    if (argument.startsWith('$name=')) {
      return argument.substring(name.length + 1);
    }
  }
  return '';
}
