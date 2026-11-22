import 'dart:io';
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../app.dart';
import 'installed_coordinator.dart';
import 'local_setup_backend.dart';

/// Checkout preview using the shell's normal configuration and service owner.
/// The laptop calls the model; selecting SSH only adds remote tools.
class LocalDesktopApplication extends StatelessWidget {
  const LocalDesktopApplication(
      {super.key, this.setupBackend, this.coordinatorFactory});

  final SetupBackend? setupBackend;
  final InstalledCoordinatorHandle Function(Json)? coordinatorFactory;

  @override
  Widget build(BuildContext context) {
    final backend = setupBackend ??
        LocalSetupBackend(
            python: const String.fromEnvironment('ODESSY_DESKTOP_PYTHON'),
            sourceRoot: const String.fromEnvironment('ODESSY_DESKTOP_ROOT'));
    return OdessyApp(
        localDesktop: true,
        restoreConfiguration: true,
        setupBackend: backend,
        coordinatorFactory: coordinatorFactory ??
            (backend is LocalSetupBackend
                ? (spec) => InstalledCoordinator(
                    python: spec['python'] as String,
                    root: '',
                    workspace: spec['workspace'] as String,
                    configuration: spec,
                    expectedFingerprint:
                        spec['expected_fingerprint'] as String?,
                    launcher: (python, arguments, cwd, environment) =>
                        Process.start(python, arguments,
                            workingDirectory: cwd,
                            environment: {
                              ...backend.processEnvironment,
                              ...environment
                            },
                            runInShell: false))
                : null));
  }
}
