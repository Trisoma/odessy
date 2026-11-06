import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/setup/setup_application.dart';

import 'setup_flow_fakes.dart';
import 'setup_usability_test.dart' show FakeAck, FakePicker;

/// Drives the packaged setup surface the way a person would: continue, continue,
/// continue. The review is expected to have run by the time the third tap settles.
Future<void> _openReview(WidgetTester tester, FlowBackend backend) async {
  await tester.pumpWidget(SetupApplication(
      mode: 'setup',
      backendFactory: () => backend,
      picker: FakePicker(),
      acknowledgement: FakeAck(true),
      onExit: () async {}));
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 50));
  for (var tap = 0; tap < 3; tap++) {
    await tester.tap(find.text('Continue'));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
  }
}

int _reviews(FlowBackend backend) => backend.requests
    .where((request) => request.parameters['action'] == 'package-review')
    .length;

/// Opens the model-and-voice step without going on to the review.
Future<void> _openVoiceStep(WidgetTester tester, FlowBackend backend) async {
  await tester.pumpWidget(SetupApplication(
      mode: 'setup',
      backendFactory: () => backend,
      picker: FakePicker(),
      acknowledgement: FakeAck(true),
      onExit: () async {}));
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 50));
  for (var tap = 0; tap < 2; tap++) {
    await tester.tap(find.text('Continue'));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
  }
}

void main() {
  testWidgets('user can cancel an in-progress voice download', (tester) async {
    final backend = FlowBackend()..pendingVoiceInstall = Completer();
    await _openVoiceStep(tester, backend);
    await tester.tap(find.byKey(const Key('setup-voice-download')));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    await tester.tap(find.text('Continue'));
    for (var i = 0; i < 8; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    await tester.tap(find.text('Install'));
    await tester.pump();
    expect(find.text('Cancel installation'), findsOneWidget);
    await tester.tap(find.text('Cancel installation'));
    for (var i = 0; i < 8; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    expect(backend.pendingVoiceInstall!.isCompleted, isTrue);
    expect(
        backend.requests
            .where((r) => r.parameters['action'] == 'package-install'),
        isEmpty);
  });
  testWidgets('selected voice installs with Odessy after confirmation',
      (tester) async {
    final backend = FlowBackend();
    await _openVoiceStep(tester, backend);
    await tester.tap(find.byKey(const Key('setup-voice-download')));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    expect(find.text('Download and install voice components'), findsNothing);
    await tester.tap(find.text('Continue'));
    for (var i = 0; i < 8; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    expect(find.textContaining('Voice components will be downloaded'),
        findsOneWidget);
    expect(
        backend.requests
            .where((r) => r.parameters['action'] == 'voice-install'),
        isEmpty);
    await tester.tap(find.text('Install'));
    for (var i = 0; i < 30; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    final actions =
        backend.requests.map((r) => r.parameters['action']).toList();
    expect(
        actions,
        containsAllInOrder([
          'voice-install',
          'package-review',
          'package-install',
          'voice-python-install'
        ]));
    final request = backend.requests
        .firstWhere((r) => r.parameters['action'] == 'package-install');
    expect(
        (request.parameters['selection'] as Map)['voice']['enabled'], isTrue);
  });
  testWidgets('opening the review step generates the review by itself',
      (tester) async {
    final backend = FlowBackend();
    await _openReview(tester, backend);
    expect(_reviews(backend), 1);
    expect(find.text('Components'), findsOneWidget);
    expect(find.text('Recalculate this review'), findsOneWidget);
    expect(find.text('Review this installation'), findsNothing);
  });

  testWidgets('a failed review explains itself and offers a retry',
      (tester) async {
    final backend = FlowBackend()
      ..failActions = {'package-review'}
      ..failureReason = 'the payload has no runtime component';
    await _openReview(tester, backend);
    expect(find.text('Review again'), findsOneWidget);
    expect(find.text('the payload has no runtime component'), findsOneWidget);
    expect(
        tester
            .widget<FilledButton>(find.widgetWithText(FilledButton, 'Install'))
            .onPressed,
        isNull,
        reason: 'Install stays disabled without a reviewed plan');

    backend.failureReason = '';
    await tester.tap(find.text('Review again'));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    expect(find.text('Components'), findsOneWidget);
    expect(_reviews(backend), 2);
  });

  testWidgets(
      'editing an option invalidates the review and re-reviews on return',
      (tester) async {
    final backend = FlowBackend();
    await _openReview(tester, backend);
    const installKey = Key('setup-model-provider');

    await tester.tap(find.text('Back'));
    await tester.pump();
    await tester.tap(find.byKey(installKey));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Configure a model later').last);
    await tester.pumpAndSettle();

    await tester.tap(find.text('Continue'));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    expect(_reviews(backend), 2,
        reason: 'the changed selection must produce a new review');
    expect(find.text('Install'), findsOneWidget);
  });

  testWidgets('a narrow window keeps navigation and errors reachable',
      (tester) async {
    tester.view.physicalSize = const Size(720, 540);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final backend = FlowBackend();
    await _openReview(tester, backend);
    expect(tester.takeException(), isNull);
    expect(find.text('Back'), findsOneWidget);
    expect(find.text('Install'), findsOneWidget);
    expect(find.text('Recalculate this review'), findsOneWidget);
  });

  testWidgets('nothing is offered when every component is already installed',
      (tester) async {
    // OpenClaw's setup checks act per case and ask nothing when there is nothing
    // to do; an installed voice folder must not offer a download again.
    final backend = FlowBackend()..voiceInstalled = true;
    await _openVoiceStep(tester, backend);
    await tester.tap(find.byKey(const Key('setup-voice-download')));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    expect(find.textContaining('already installed'), findsOneWidget);
    expect(find.text('Download and install voice components'), findsNothing);
  });

  testWidgets('a failed voice download allows retry without installing Odessy',
      (tester) async {
    final backend = FlowBackend()
      ..failActions = {'voice-install'}
      ..failureReason = 'the connection was interrupted';
    await _openVoiceStep(tester, backend);
    await tester.tap(find.byKey(const Key('setup-voice-download')));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    await tester.tap(find.text('Continue'));
    for (var i = 0; i < 8; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    await tester.tap(find.text('Install'));
    for (var i = 0; i < 12; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    expect(
        find.textContaining('the connection was interrupted'), findsOneWidget);
    expect(
        backend.requests
            .where((r) => r.parameters['action'] == 'package-install'),
        isEmpty);
    backend.failureReason = '';
    await tester.tap(find.text('Install'));
    for (var i = 0; i < 30; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    expect(
        backend.requests
            .where((r) => r.parameters['action'] == 'package-install'),
        hasLength(1));
    expect(backend.voiceDependenciesInstalled, isTrue);
  });

  testWidgets('large text keeps the voice controls visible and reachable',
      (tester) async {
    tester.view.physicalSize = const Size(720, 540);
    tester.view.devicePixelRatio = 1;
    tester.platformDispatcher.textScaleFactorTestValue = 2;
    addTearDown(() {
      tester.view.resetPhysicalSize();
      tester.view.resetDevicePixelRatio();
      tester.platformDispatcher.clearTextScaleFactorTestValue();
    });
    final backend = FlowBackend();
    await _openVoiceStep(tester, backend);
    final overflow = tester.takeException();
    expect(overflow, isNull,
        reason:
            'no overflow with double-size text at 720x540: ${overflow is FlutterError ? overflow.toStringDeep() : overflow}');

    final toggle = find.byKey(const Key('setup-voice-download'));
    expect(toggle, findsOneWidget);
    expect(tester.widget<SwitchListTile>(toggle).onChanged, isNotNull,
        reason: 'the opt-in stays operable at large text');

    await tester.ensureVisible(toggle);
    await tester.pumpAndSettle();
    final before = FocusManager.instance.primaryFocus;
    for (var i = 0; i < 30; i++) {
      if (!identical(FocusManager.instance.primaryFocus, before)) break;
      await tester.sendKeyEvent(LogicalKeyboardKey.tab);
      await tester.pump();
    }
    expect(identical(FocusManager.instance.primaryFocus, before), isFalse,
        reason: 'Tab must move focus through the setup surface');
  });
}
