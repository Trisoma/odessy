import 'dart:async';
import 'dart:io';
import 'dart:ui' show AppExitResponse;

import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';

import '../../features/setup/controllers/installed_manage_controller.dart';
import '../../features/setup/presentation/get_started_page.dart';
import '../../features/setup/presentation/manage_page.dart';
import '../app.dart';
import '../theme.dart';
import 'installation_location.dart';
import 'installed_coordinator.dart';
import 'launch_acknowledgement.dart';
import 'local_setup_backend.dart';
import 'native_picker.dart';

enum InstalledPhase { checking, configure, starting, ready, failed }

/// Resolves the recorded installation, then starts its coordinator only when a
/// model is configured. A missing model opens management; it never falls back to
/// demo mode and never downloads or selects a model on the user's behalf.
class InstalledStartup extends ChangeNotifier {
  InstalledStartup({
    required this.backend,
    required this.installRoot,
    InstalledCoordinatorHandle Function(Json spec)? coordinatorFactory,
  }) : coordinatorFactory = coordinatorFactory ?? defaultCoordinator;

  final SetupBackend backend;
  final String installRoot;
  final InstalledCoordinatorHandle Function(Json spec) coordinatorFactory;

  Json spec = const {};
  InstalledPhase phase = InstalledPhase.checking;
  String message = '';
  LocalApi? api;
  InstalledCoordinatorHandle? _coordinator;
  bool _disposed = false;
  int _epoch = 0;

  static InstalledCoordinator defaultCoordinator(Json spec) => InstalledCoordinator(
      python: spec['python'] as String,
      root: spec['root'] as String,
      workspace: spec['workspace'] as String);

  Future<void> begin() async {
    if (_disposed) return;
    final epoch = ++_epoch;
    spec = const {};
    api = null;
    message = '';
    phase = InstalledPhase.checking;
    notifyListeners();
    try {
      final reply = await backend.execute(SetupRequest(
          id: 'installed-${DateTime.now().microsecondsSinceEpoch}',
          operation: 'manage',
          selection: const {},
          parameters: {
            'action': 'installed-spec',
            'root': installRoot,
          }));
      if (!reply.ok) {
        throw StateError(reply.reason.isNotEmpty
            ? reply.reason
            : 'The installed state could not be read.');
      }
      if (_disposed || epoch != _epoch) return;
      spec = reply.result;
      final role = spec['role'];
      if (role == 'execution') {
        phase = InstalledPhase.configure;
        message =
            'This computer is an execution node. Its coordinator is configured elsewhere; no model is started here.';
        notifyListeners();
        return;
      }
      if (spec['model_configured'] != true) {
        phase = InstalledPhase.configure;
        message =
            'No model is configured yet. Choose an existing model in configuration, or keep it unconfigured. Nothing is downloaded or started.';
        notifyListeners();
        return;
      }
      phase = InstalledPhase.starting;
      notifyListeners();
      await _stopCoordinator();
      final coordinator = coordinatorFactory(spec);
      _coordinator = coordinator;
      final client = await coordinator.start();
      if (_disposed || epoch != _epoch) {
        await coordinator.stop();
        return;
      }
      api = client;
      phase = InstalledPhase.ready;
      notifyListeners();
    } catch (failure) {
      if (_disposed || epoch != _epoch) return;
      await _stopCoordinator();
      phase = InstalledPhase.failed;
      message = failure is CoordinatorStartFailure
          ? failure.message
          : failure.toString().replaceFirst('Bad state: ', '');
      notifyListeners();
    }
  }

  Future<void> _stopCoordinator() async {
    final coordinator = _coordinator;
    _coordinator = null;
    try {
      await coordinator?.stop();
    } catch (_) {/* The child is already gone. */}
  }

  Future<void> shutdown() async {
    _disposed = true;
    await _stopCoordinator();
    await backend.close();
  }

  @override
  void dispose() {
    _disposed = true;
    unawaited(_stopCoordinator());
    super.dispose();
  }
}

class InstalledApplication extends StatefulWidget {
  const InstalledApplication({
    super.key,
    required this.installRoot,
    this.launchId = '',
    this.backend,
    this.startupFactory,
    this.picker = const UnavailablePicker(),
    this.acknowledgement,
    this.onExit,
  });

  final String installRoot;

  /// The identifier setup passed with `--launch-id`; empty when opened directly.
  final String launchId;
  final SetupBackend? backend;
  final InstalledStartup Function(SetupBackend backend)? startupFactory;
  final NativePicker picker;
  final LaunchAcknowledgement? acknowledgement;
  final Future<void> Function()? onExit;

  @override
  State<InstalledApplication> createState() => _InstalledApplicationState();
}

class _InstalledApplicationState extends State<InstalledApplication> {
  late final SetupBackend _backend;
  late final InstalledStartup _startup;
  late final AppLifecycleListener _lifecycle;
  InstalledManageController? _manage;
  String _manageRoot = '';
  final Set<String> _acknowledged = {};

  @override
  void initState() {
    super.initState();
    _backend = widget.backend ?? LocalSetupBackend();
    _startup = (widget.startupFactory ?? _defaultStartup)(_backend);
    _startup.addListener(_changed);
    _lifecycle = AppLifecycleListener(onExitRequested: () async {
      await _startup.shutdown();
      return AppExitResponse.exit;
    });
    unawaited(_startup.begin());
  }

  InstalledStartup _defaultStartup(SetupBackend backend) =>
      InstalledStartup(backend: backend, installRoot: widget.installRoot);

  void _changed() {
    if (!mounted) return;
    final root = (_startup.spec['root'] as String?) ?? '';
    if (root.isNotEmpty && root != _manageRoot) {
      _manageRoot = root;
      _manage = InstalledManageController(_backend, spec: _startup.spec)..load();
    }
    setState(() {});
    final phase = _startup.phase;
    final version = _startup.spec['version'];
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || _startup.phase != phase || _startup.spec['version'] != version) return;
      _acknowledge();
    });
  }

  /// Tells setup that this window resolved its installation and either reached the
  /// coordinator or is showing the configuration-required state. Process creation
  /// alone is not readiness, so this is the only success signal. A failed start is
  /// reported as well, so setup stops waiting instead of showing a timeout.
  void _acknowledge() {
    final launchId = widget.launchId;
    final root = _startup.spec['root'];
    if (launchId.isEmpty || root is! String || root.isEmpty) return;
    final state = switch (_startup.phase) {
      InstalledPhase.ready => launchReady,
      InstalledPhase.configure => launchConfigurationRequired,
      InstalledPhase.failed => launchFailed,
      _ => null,
    };
    if (state == null || !_acknowledged.add('$launchId:$state')) return;
    final acknowledgement =
        widget.acknowledgement ?? BackendLaunchAcknowledgement(_backend, root: root);
    unawaited(acknowledgement.acknowledge(launchId, state: state).catchError((_) {
      // A rejected receipt only means setup keeps waiting; nothing is granted here.
    }));
  }

  @override
  void dispose() {
    _lifecycle.dispose();
    _startup.removeListener(_changed);
    _startup.dispose();
    _manage?.dispose();
    super.dispose();
  }

  Future<void> _close() async {
    await _startup.shutdown();
    final exit = widget.onExit;
    if (exit != null) {
      await exit();
    } else {
      exitApp();
    }
  }

  @override
  Widget build(BuildContext context) {
    final api = _startup.api;
    if (_startup.phase == InstalledPhase.ready && api != null) {
      return OdessyApp(api: api, installed: _startup.spec);
    }
    return MaterialApp(
        title: 'Odessy',
        theme: odessyTheme(Brightness.dark),
        home: Scaffold(
            body: SafeArea(
                child: Center(
                    child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 940),
                        child: _content(context))))));
  }

  Widget _content(BuildContext context) {
    switch (_startup.phase) {
      case InstalledPhase.checking:
      case InstalledPhase.starting:
        return Column(mainAxisSize: MainAxisSize.min, children: [
          const SizedBox(width: 220, child: LinearProgressIndicator()),
          const SizedBox(height: 18),
          Text(_startup.phase == InstalledPhase.checking
              ? 'Checking the installed Odessy files…'
              : 'Starting the local coordinator…'),
          const SizedBox(height: 8),
          const Text(
              'The coordinator listens only on this computer and requires a per-launch token.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 12)),
          const SizedBox(height: 18),
          TextButton(onPressed: _close, child: const Text('Close')),
        ]);
      case InstalledPhase.configure:
        final manage = _manage;
        if (manage == null) {
          return const Center(child: CircularProgressIndicator());
        }
        return Column(children: [
          Expanded(
              child: GetStartedPage(
            controller: manage,
            picker: widget.picker,
            onStart: () => _startup.begin(),
            onClose: _close,
            coordinatorReady: false,
          )),
          Padding(
              padding: const EdgeInsets.symmetric(horizontal: 28),
              child: Row(children: [
                TextButton(
                    onPressed: _openManage,
                    child: const Text('Maintenance and repair')),
                const Spacer(),
                TextButton(onPressed: _close, child: const Text('Close')),
              ])),
          const SizedBox(height: 12),
        ]);
      case InstalledPhase.failed:
        return Padding(
            padding: const EdgeInsets.all(32),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Icon(Icons.error_outline, size: 40),
              const SizedBox(height: 16),
              Text('Odessy could not start',
                  style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 10),
              SelectableText(_startup.message, textAlign: TextAlign.center),
              const SizedBox(height: 20),
              Wrap(spacing: 10, runSpacing: 10, children: [
                FilledButton(
                    onPressed: () => _startup.begin(),
                    child: const Text('Retry')),
                OutlinedButton(
                    onPressed: _openManage, child: const Text('Manage installation')),
                TextButton(onPressed: _close, child: const Text('Close')),
              ]),
            ]));
      case InstalledPhase.ready:
        return const SizedBox.shrink();
    }
  }

  void _openManage() {
    final manage = _manage;
    if (manage == null) return;
    Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (context) => Scaffold(
            appBar: AppBar(title: const Text('Manage Odessy')),
            body: ManagePage(
                controller: manage,
                external: runningOutsideInstallation(_manageRoot),
                onStart: () => _startup.begin(),
                onOpenMaintenance: () => manage.openMaintenance()))));
  }
}

/// Closes the desktop process. Desktop Flutter has no navigator pop for this.
void exitApp() {
  exit(0);
}
