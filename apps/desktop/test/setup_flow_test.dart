import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/setup/setup_application.dart';
import 'package:odessy_desktop/features/setup/controllers/package_setup_controller.dart';

import 'setup_flow_fakes.dart';
import 'setup_usability_test.dart' show FakeAck, FakePicker;

/// The install screen keeps a one-second ticker for elapsed time, so
/// `pumpAndSettle` would never settle. Pump a bounded amount instead.
Future<void> settle(WidgetTester tester) async {
  for (var i = 0; i < 40; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  Future<void> pumpSetup(
    WidgetTester tester,
    FlowBackend backend, {
    FakePicker? picker,
    FakeAck? ack,
    void Function()? onExit,
    String mode = 'setup',
    String installRoot = '',
  }) async {
    tester.view.physicalSize = const Size(960, 700);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(SetupApplication(
        mode: mode,
        installRoot: installRoot,
        backendFactory: () => backend,
        picker: picker ?? FakePicker(),
        acknowledgement: ack ?? FakeAck(true),
        onExit: () async => onExit?.call()));
    await settle(tester);
  }

  Future<void> advanceToReview(WidgetTester tester) async {
    await tester.tap(find.text('Continue'));
    await settle(tester);
    await tester.tap(find.text('Continue'));
    await settle(tester);
    await tester.tap(find.text('Continue'));
    await settle(tester);
    await tester.tap(find.text('Review this installation'));
    await settle(tester);
  }

  Future<void> install(WidgetTester tester) async {
    await tester.tap(find.text('Install'));
    await settle(tester);
  }

  // A failed review writes nothing, so the step has to say why. Before this the
  // button looked unresponsive: no plan, Install disabled, no explanation.
  testWidgets('a failed review explains itself and keeps Install disabled',
      (tester) async {
    await pumpSetup(tester, _FailingReviewBackend());
    await tester.tap(find.text('Continue'));
    await settle(tester);
    await tester.tap(find.text('Continue'));
    await settle(tester);
    await tester.tap(find.text('Continue'));
    await settle(tester);
    await tester.tap(find.text('Review this installation'));
    await settle(tester);
    // The reason appears in the message and again in its details.
    expect(find.textContaining('setup payload lacks required components'),
        findsWidgets);
    expect(find.text('Review again'), findsOneWidget);
    expect(
        tester
            .widget<FilledButton>(find.widgetWithText(FilledButton, 'Install'))
            .onPressed,
        isNull);
  });

  testWidgets('browse fills the folder; a cancelled dialog changes nothing',
      (tester) async {
    final backend = FlowBackend();
    final picker = FakePicker();
    await pumpSetup(tester, backend, picker: picker);
    await tester.tap(find.text('Continue'));
    await settle(tester);
    String path() => tester
        .widget<TextField>(find.byKey(const Key('setup-location')))
        .controller!
        .text;
    final initial = path();
    await tester.tap(find.text('Browse…'));
    await settle(tester);
    expect(path(), initial, reason: 'a cancelled picker must not change the path');
    picker.directory = r'C:\Users\me\My Odessy Install (test)';
    await tester.tap(find.text('Browse…'));
    await settle(tester);
    expect(path(), r'C:\Users\me\My Odessy Install (test)');
    expect(backend.requests.last.parameters['root'],
        r'C:\Users\me\My Odessy Install (test)');
  });

  testWidgets('long path stays on one line without overflow', (tester) async {
    final backend = FlowBackend();
    await pumpSetup(tester, backend);
    tester.view.physicalSize = const Size(720, 540);
    await tester.tap(find.text('Continue'));
    await settle(tester);
    await tester.enterText(
        find.byKey(const Key('setup-location')),
        r'C:\Users\someone\OneDrive - Contoso Corporation\Documents and Settings\Odessy application installation folder');
    await settle(tester);
    expect(tester.takeException(), isNull);
    final field =
        tester.widget<TextField>(find.byKey(const Key('setup-location')));
    expect(field.maxLines, 1);
  });

  testWidgets('review shows the summary and binds the exact plan to Install',
      (tester) async {
    final backend = FlowBackend();
    await pumpSetup(tester, backend);
    await advanceToReview(tester);
    expect(find.text('Review before installing'), findsOneWidget);
    expect(find.text('Configure later — nothing is started'), findsOneWidget);
    await tester.tap(find.text('Install'));
    await settle(tester);
    final install = backend.requests
        .firstWhere((r) => r.parameters['action'] == 'package-install');
    expect(install.parameters['confirm'], 'plan-1');
    expect(find.text('Odessy is installed'), findsOneWidget);
  });

  testWidgets('finished setup keeps the window when launch is not acknowledged',
      (tester) async {
    final backend = FlowBackend();
    var exited = false;
    await pumpSetup(tester, backend,
        ack: FakeAck(false), onExit: () => exited = true);
    await advanceToReview(tester);
    await install(tester);
    expect(find.text('Odessy is installed'), findsOneWidget);
    await tester.tap(find.text('Open Odessy and finish'));
    await settle(tester);
    expect(exited, isFalse, reason: 'setup must stay visible');
    expect(find.textContaining('has not reported readiness'), findsOneWidget);
    expect(find.text('Open Odessy again'), findsOneWidget);
  });

  testWidgets('acknowledged hand-off closes setup', (tester) async {
    final backend = FlowBackend();
    var exited = false;
    await pumpSetup(tester, backend,
        ack: FakeAck(true), onExit: () => exited = true);
    await advanceToReview(tester);
    await install(tester);
    await tester.tap(find.text('Open Odessy and finish'));
    await settle(tester);
    expect(
        backend.requests.map((r) => r.parameters['action']),
        contains('package-launch'),
        reason: 'the finish action must ask the bridge to launch');
    expect(exited, isTrue);
  });

  testWidgets('a failed launch keeps a retry action and no false success',
      (tester) async {
    final backend = FlowBackend(launchResponse: const {'launched': false});
    var exited = false;
    await pumpSetup(tester, backend, onExit: () => exited = true);
    await advanceToReview(tester);
    await install(tester);
    await tester.tap(find.text('Open Odessy and finish'));
    await settle(tester);
    expect(exited, isFalse);
    expect(find.textContaining('did not start'), findsOneWidget);
  });

  testWidgets('an existing installation blocks a second install here',
      (tester) async {
    final backend = FlowBackend()..installed = true;
    await pumpSetup(tester, backend);
    await tester.tap(find.text('Continue'));
    await settle(tester);
    expect(find.text('Odessy is already installed in this folder.'),
        findsOneWidget);
    expect(find.text('Install'), findsNothing);
  });

  test('engine output is appended once, in order, and labelled by stream',
      () async {
    final backend = FlowBackend()
      ..statusLogs = const [
        {'seq': 1, 'stage': 'dependencies', 'stream': 'stdout', 'line': 'Collecting wheel'},
        {'seq': 2, 'stage': 'dependencies', 'stream': 'stderr', 'line': 'index unreachable'},
      ];
    final controller = PackageSetupController(backend);
    addTearDown(controller.dispose);
    await controller.readProgress();
    await controller.readProgress();
    final lines = controller.output;
    expect(lines.where((line) => line.contains('Collecting wheel')), hasLength(1),
        reason: 'a repeated poll must not duplicate a line already shown');
    expect(lines.any((line) => line.contains('Installing dependencies: Collecting wheel')),
        isTrue);
    expect(lines.any((line) => line.contains('Installing dependencies error: index unreachable')),
        isTrue,
        reason: 'stderr must be distinguishable from ordinary output');
  });

  test('a truncated journal says so instead of pretending output is complete',
      () async {
    final backend = FlowBackend()..statusTruncated = true;
    final controller = PackageSetupController(backend);
    addTearDown(controller.dispose);
    await controller.readProgress();
    await controller.readProgress();
    expect(
        controller.output
            .where((line) => line.contains('no longer in the bounded journal')),
        hasLength(1));
  });
}

/// Every other action succeeds; only the review step fails, as a payload that
/// cannot satisfy the chosen role would.
class _FailingReviewBackend extends FlowBackend {
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    if (request.parameters['action'] == 'package-review') {
      requests.add(request);
      return SetupReply.fromJson({
        'id': request.id,
        'status': 'error',
        'reason': 'setup payload lacks required components: desktop',
      }, request.id);
    }
    return super.execute(request);
  }
}
