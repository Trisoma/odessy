import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../app.dart';
import '../theme.dart';
import 'installed_coordinator.dart';

/// Local development entry with an owned, authenticated real service.
class LocalDesktopApplication extends StatefulWidget {
  const LocalDesktopApplication({super.key, this.coordinator});
  final InstalledCoordinatorHandle? coordinator;
  @override
  State<LocalDesktopApplication> createState() =>
      _LocalDesktopApplicationState();
}

class _LocalDesktopApplicationState extends State<LocalDesktopApplication> {
  static const root = String.fromEnvironment('ODESSY_DESKTOP_ROOT');
  static const python = String.fromEnvironment('ODESSY_DESKTOP_PYTHON');
  late final InstalledCoordinatorHandle coordinator;
  LocalApi? api;
  String? failure;
  bool starting = true;

  @override
  void initState() {
    super.initState();
    coordinator = widget.coordinator ??
        InstalledCoordinator(
            python: python,
            root: root,
            workspace: root,
            launcher: (interpreter, arguments, cwd, environment) {
              final port = arguments[arguments.indexOf('--port') + 1];
              final deps = '$root/build/local-desktop/dependencies';
              final paths = [
                '$root/src',
                deps,
                '$deps/win32',
                '$deps/win32/lib',
                '$deps/pywin32_system32'
              ];
              return Process.start(
                  interpreter,
                  [
                    '-m',
                    'odessy.application.local_desktop',
                    '--root',
                    root,
                    '--port',
                    port
                  ],
                  workingDirectory: root,
                  environment: {
                    ...environment,
                    'PYTHONPATH': paths.join(Platform.isWindows ? ';' : ':')
                  },
                  runInShell: false);
            });
    unawaited(_start());
  }

  Future<void> _start() async {
    setState(() {
      starting = true;
      failure = null;
    });
    try {
      final result = await coordinator.start();
      if (!mounted) {
        result.close();
        await coordinator.stop();
        return;
      }
      setState(() {
        api = result;
        starting = false;
      });
    } catch (error) {
      await coordinator.stop();
      if (mounted) {
        setState(() {
          failure = error.toString();
          starting = false;
        });
      }
    }
  }

  @override
  void dispose() {
    unawaited(coordinator.stop());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final client = api;
    if (client != null) {
      return OdessyApp(
          api: client, localDesktop: true, onExitApproved: coordinator.stop);
    }
    return MaterialApp(
        title: 'Odessy',
        debugShowCheckedModeBanner: false,
        theme: odessyTheme(Brightness.light),
        darkTheme: odessyTheme(Brightness.dark),
        themeMode: ThemeMode.dark,
        home: Scaffold(
            body: Center(
                child: Padding(
                    padding: const EdgeInsets.all(32),
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                      if (starting) ...[
                        const CircularProgressIndicator(
                            semanticsLabel: 'Starting local service'),
                        const SizedBox(height: 16),
                      ],
                      Text(starting
                          ? 'Starting local service…'
                          : 'Local service could not start'),
                      if (failure != null) SelectableText(failure!),
                      if (!starting)
                        TextButton(
                            onPressed: _start, child: const Text('Retry')),
                    ])))));
  }
}
