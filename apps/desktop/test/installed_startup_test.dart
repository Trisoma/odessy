import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/setup/installed_coordinator.dart';
import 'package:odessy_desktop/app/setup/installed_startup.dart';
import 'package:odessy_desktop/app/setup/launch_acknowledgement.dart';
import 'package:flutter/material.dart';

class ControlledStartup extends InstalledStartup {
  ControlledStartup(SetupBackend backend)
      : super(backend: backend, installRoot: 'C:/Odessy/App');
  @override
  Future<void> begin() async {}
  void resolve() {
    spec = specWith();
    phase = InstalledPhase.configure;
    notifyListeners();
  }
}

class RecordingAcknowledgement implements LaunchAcknowledgement {
  final states = <String>[];
  @override
  Future<void> acknowledge(String launchId, {required String state}) async =>
      states.add(state);
  @override
  Future<String?> waitFor(String launchId,
          {Duration timeout = const Duration(seconds: 45),
          Duration interval = const Duration(milliseconds: 250)}) async =>
      null;
}

class LoadingBackend extends Backend {
  LoadingBackend() : super(specWith());
  final loading = Completer<void>();
  bool reading = false;
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    if (request.parameters['action'] == 'installed-spec') {
      reading = true;
      await loading.future;
      reading = false;
    }
    if (reading) throw StateError('Setup helper is unavailable or busy.');
    return super.execute(request);
  }
}

class BusyAcknowledgement extends RecordingAcknowledgement {
  BusyAcknowledgement(this.backend);
  final LoadingBackend backend;
  @override
  Future<void> acknowledge(String launchId, {required String state}) async {
    if (backend.reading) {
      throw StateError('Setup helper is unavailable or busy.');
    }
    states.add(state);
  }
}

class Backend implements SetupBackend {
  Backend(this.spec);
  Json spec;
  String failure = '';
  final requests = <SetupRequest>[];
  bool closed = false;
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    if (failure.isNotEmpty) {
      return SetupReply.fromJson({
        'id': request.id,
        'status': 'error',
        'reason': failure,
      }, request.id);
    }
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': spec,
    }, request.id);
  }

  @override
  Future<void> cancel() async {}
  @override
  Future<void> close() async => closed = true;
}

class Coordinator implements InstalledCoordinatorHandle {
  Coordinator({this.failure});
  final Object? failure;
  bool started = false, stopped = false;
  @override
  Future<LocalApi> start() async {
    if (failure != null) {
      started = true;
      throw failure!;
    }
    started = true;
    return LocalApi(Uri.parse('http://127.0.0.1:8765'), 'token');
  }

  @override
  Future<void> stop() async => stopped = true;
}

Json specWith({bool model = false, String role = 'combined'}) => {
      'root': 'C:/Odessy/App',
      'version': 'a' * 32,
      'python': 'C:/Odessy/App/versions/a/Scripts/python.exe',
      'desktop': 'C:/Odessy/App/versions/a/desktop/odessy_desktop.exe',
      'role': role,
      'model_configured': model,
      'workspace': 'C:/Users/me/Documents/Odessy',
    };

void main() {
  testWidgets('acknowledgement waits for concurrent management inspection',
      (tester) async {
    final backend = LoadingBackend();
    final startup = ControlledStartup(backend);
    final ack = BusyAcknowledgement(backend);
    await tester.pumpWidget(InstalledApplication(
        installRoot: 'C:/Odessy/App',
        launchId: 'a' * 32,
        backend: backend,
        startupFactory: (_) => startup,
        acknowledgement: ack));
    startup.resolve();
    await tester.pump();
    expect(ack.states, isEmpty);
    backend.loading.complete();
    await tester.pump();
    await tester.pump();
    expect(ack.states, [launchConfigurationRequired]);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('readiness is acknowledged only after the configured frame',
      (tester) async {
    final backend = Backend(specWith());
    final startup = ControlledStartup(backend);
    final ack = RecordingAcknowledgement();
    await tester.pumpWidget(InstalledApplication(
        installRoot: 'C:/Odessy/App',
        launchId: 'a' * 32,
        backend: backend,
        startupFactory: (_) => startup,
        acknowledgement: ack));
    startup.resolve();
    expect(ack.states, isEmpty);
    await tester.pump();
    expect(ack.states, [launchConfigurationRequired]);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('disposed window never acknowledges queued readiness',
      (tester) async {
    final backend = Backend(specWith());
    final startup = ControlledStartup(backend);
    final ack = RecordingAcknowledgement();
    await tester.pumpWidget(InstalledApplication(
        installRoot: 'C:/Odessy/App',
        launchId: 'a' * 32,
        backend: backend,
        startupFactory: (_) => startup,
        acknowledgement: ack));
    startup.resolve();
    await tester.pumpWidget(const SizedBox());
    expect(ack.states, isEmpty);
  });
  test('an unconfigured installation opens management without a coordinator',
      () async {
    final backend = Backend(specWith());
    final coordinators = <Coordinator>[];
    final startup = InstalledStartup(
        backend: backend,
        installRoot: 'C:/Odessy/App',
        coordinatorFactory: (spec) {
          final coordinator = Coordinator();
          coordinators.add(coordinator);
          return coordinator;
        });
    await startup.begin();
    expect(startup.phase, InstalledPhase.configure);
    expect(startup.api, isNull);
    expect(coordinators, isEmpty);
    expect(startup.message, contains('No model is configured'));
    await startup.shutdown();
  });

  test('a configured installation starts one owned coordinator', () async {
    final backend = Backend(specWith(model: true));
    final coordinator = Coordinator();
    final startup = InstalledStartup(
        backend: backend,
        installRoot: 'C:/Odessy/App',
        coordinatorFactory: (spec) => coordinator);
    await startup.begin();
    expect(startup.phase, InstalledPhase.ready);
    expect(startup.api, isNotNull);
    expect(coordinator.started, isTrue);
    await startup.shutdown();
    expect(coordinator.stopped, isTrue);
    expect(backend.closed, isTrue);
  });

  test('an execution node never starts a coordinator', () async {
    final backend = Backend(specWith(model: true, role: 'execution'));
    final startup = InstalledStartup(
        backend: backend,
        installRoot: 'C:/Odessy/App',
        coordinatorFactory: (spec) => Coordinator());
    await startup.begin();
    expect(startup.phase, InstalledPhase.configure);
    expect(startup.api, isNull);
    expect(startup.message, contains('execution node'));
    await startup.shutdown();
  });

  test('a startup failure is explicit and retryable', () async {
    final backend = Backend(specWith(model: true));
    var attempt = 0;
    final startup = InstalledStartup(
        backend: backend,
        installRoot: 'C:/Odessy/App',
        coordinatorFactory: (spec) => Coordinator(
            failure: attempt++ == 0
                ? const CoordinatorStartFailure(
                    'The local coordinator stopped.')
                : null));
    await startup.begin();
    expect(startup.phase, InstalledPhase.failed);
    expect(startup.message, 'The local coordinator stopped.');
    expect(startup.api, isNull);
    await startup.begin();
    expect(startup.phase, InstalledPhase.ready);
    await startup.shutdown();
  });

  test('a refused installed state is reported, never replaced by demo mode',
      () async {
    final backend = Backend(specWith(model: true))
      ..failure = 'installed files were modified or damaged';
    final coordinators = <Coordinator>[];
    final startup = InstalledStartup(
        backend: backend,
        installRoot: 'C:/Odessy/App',
        coordinatorFactory: (spec) {
          coordinators.add(Coordinator());
          return coordinators.last;
        });
    await startup.begin();
    expect(startup.phase, InstalledPhase.failed);
    expect(startup.message, contains('damaged'));
    expect(startup.api, isNull);
    expect(coordinators, isEmpty);
    await startup.shutdown();
  });
}
