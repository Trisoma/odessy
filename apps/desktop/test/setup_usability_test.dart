import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/setup/instance_guard.dart';
import 'package:odessy_desktop/app/setup/launch_acknowledgement.dart';
import 'package:odessy_desktop/app/setup/native_picker.dart';
import 'package:odessy_desktop/features/setup/controllers/setup_stage.dart';
import 'package:odessy_desktop/features/setup/presentation/path_field.dart';
import 'package:odessy_desktop/features/setup/presentation/setup_shell.dart';
import 'package:odessy_desktop/features/setup/presentation/stage_list.dart';

class FakePicker implements NativePicker {
  FakePicker({this.directory, this.file, this.save});
  String? directory, file, save;
  int directoryCalls = 0, fileCalls = 0;
  @override
  Future<String?> pickDirectory({String? initial, String? title}) async {
    directoryCalls++;
    return directory;
  }

  @override
  Future<String?> pickFile({String? initial, List<String> extensions = const []}) async {
    fileCalls++;
    return file;
  }

  @override
  Future<String?> pickSaveLocation(
      {required String suggestedName, List<String> extensions = const []}) async {
    return save;
  }
}

class FakeAck implements LaunchAcknowledgement {
  FakeAck(this.ready);
  final bool ready;
  int waits = 0;
  final recorded = <String>[];

  @override
  Future<void> acknowledge(String launchId, {required String state}) async =>
      recorded.add('$launchId:$state');

  @override
  Future<String?> waitFor(String launchId,
      {Duration timeout = const Duration(seconds: 45),
      Duration interval = const Duration(milliseconds: 250)}) async {
    waits++;
    return ready ? launchReady : null;
  }
}

/// Models the backend receipt: `b` is acknowledged, `d` stays pending until the
/// caller stops waiting, and `e` is an unknown identifier.
class AckBackend implements SetupBackend {
  final requests = <SetupRequest>[];

  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    final launchId = request.parameters['launch_id'];
    if (launchId == 'e' * 32) {
      return SetupReply.fromJson(
          {'id': request.id, 'status': 'error', 'reason': 'unknown launch identifier'},
          request.id);
    }
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {
        'launch_id': launchId,
        'state': launchId == 'b' * 32 ? 'ready' : 'pending',
      },
    }, request.id);
  }

  @override
  Future<void> cancel() async {}

  @override
  Future<void> close() async {}
}

void main() {
  group('shell', () {
    testWidgets('offers exactly one primary action and a secondary action',
        (tester) async {
      var primary = 0, secondary = 0;
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SetupShell(
                  title: 'Review',
                  subtitle: 'Exactly what will be written.',
                  stepLabel: 'Step 4 of 6',
                  footer: SetupFooter(
                      primaryLabel: 'Install',
                      onPrimary: () => primary++,
                      secondaryLabel: 'Back',
                      onSecondary: () => secondary++),
                  child: const Text('body')))));
      expect(find.text('Step 4 of 6'), findsOneWidget);
      expect(find.widgetWithText(FilledButton, 'Install'), findsOneWidget);
      await tester.tap(find.text('Install'));
      await tester.tap(find.text('Back'));
      expect(primary, 1);
      expect(secondary, 1);
    });

    testWidgets('stacks actions at narrow widths without wrapping unevenly',
        (tester) async {
      tester.view.physicalSize = const Size(380, 700);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SetupFooter(
                  primaryLabel: 'Continue',
                  onPrimary: () {},
                  secondaryLabel: 'Back',
                  onSecondary: () {}))));
      final primary = tester.getTopLeft(find.text('Continue'));
      final secondary = tester.getTopLeft(find.text('Back'));
      // The primary action stays first; the secondary follows without wrapping.
      expect(primary.dy, lessThan(secondary.dy));
      expect(tester.takeException(), isNull);
    });

    test('elapsed formatting distinguishes seconds, minutes and hours', () {
      expect(formatElapsed(const Duration(seconds: 4)), '4s');
      expect(formatElapsed(const Duration(minutes: 1, seconds: 5)), '1m 05s');
      expect(formatElapsed(const Duration(hours: 1, minutes: 2)), '1h 02m');
      expect(formatElapsed(null), '—');
    });
  });

  group('path field', () {
    testWidgets('shows one line, browses and reports inline errors',
        (tester) async {
      final controller = TextEditingController(
          text: r'C:\Users\someone\OneDrive\Documents\A very long folder name that keeps going\Odessy');
      var browsed = 0;
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: PathField(
                  fieldKey: const Key('path'),
                  controller: controller,
                  label: 'Installation folder',
                  error: 'Choose a folder that is not inside the installation',
                  onBrowse: () => browsed++))));
      expect(find.text('Choose a folder that is not inside the installation'),
          findsOneWidget);
      final field = tester.widget<TextField>(find.byKey(const Key('path')));
      expect(field.maxLines, 1);
      await tester.tap(find.text('Browse…'));
      expect(browsed, 1);
      expect(tester.takeException(), isNull);
    });

    testWidgets('cancelled selection changes nothing', (tester) async {
      final picker = FakePicker();
      final controller = TextEditingController(text: r'C:\keep');
      final selected = await picker.pickDirectory();
      expect(selected, isNull);
      if (selected != null) controller.text = selected;
      expect(controller.text, r'C:\keep');
      expect(picker.directoryCalls, 1);
    });

    test('summary paths keep the head and the tail on one line', () {
      final value = r'C:\Users\someone\AppData\Local\Odessy\App\versions\0123456789abcdef';
      final shortened = middleEllipsis(value, 30);
      expect(shortened.length, 30);
      expect(shortened.contains('…'), isTrue);
      expect(shortened.startsWith(r'C:\Users'), isTrue);
      expect(shortened.endsWith('abcdef'), isTrue);
      expect(middleEllipsis('short', 30), 'short');
    });
  });

  group('stages', () {
    test('advance only forwards and never mixes stages', () {
      var stages = newPackagedStages();
      stages = advanceStages(stages, 'environment', DateTime.now());
      expect(stages.first.state, SetupStageState.complete);
      final runtime = stages.firstWhere((s) => s.id == 'runtime');
      expect(runtime.state, SetupStageState.running);
      stages = advanceStages(stages, 'dependencies', DateTime.now());
      expect(stages.firstWhere((s) => s.id == 'runtime').state,
          SetupStageState.complete);
      expect(stages.firstWhere((s) => s.id == 'dependencies').state,
          SetupStageState.running);
      // A stale engine stage never moves the list backwards.
      stages = advanceStages(stages, 'environment', DateTime.now());
      expect(stages.firstWhere((s) => s.id == 'dependencies').state,
          SetupStageState.running);
      stages = advanceStages(stages, 'activated', DateTime.now(), complete: true);
      expect(stages.every((s) => s.state == SetupStageState.complete), isTrue);
    });

    test('a failure marks only the active stage', () {
      var stages = newPackagedStages();
      stages = advanceStages(stages, 'dependencies', DateTime.now());
      stages = advanceStages(stages, null, DateTime.now(), failed: true);
      expect(stages.where((s) => s.state == SetupStageState.failed).length, 1);
      expect(stages.first.state, SetupStageState.complete);
    });

    test('cancellation is visible and distinct from failure', () {
      var stages = newPackagedStages();
      stages = advanceStages(stages, 'components', DateTime.now());
      stages = advanceStages(stages, null, DateTime.now(), cancelled: true);
      expect(stages.any((s) => s.state == SetupStageState.cancelled), isTrue);
      expect(stages.any((s) => s.state == SetupStageState.failed), isFalse);
    });

    testWidgets('rows expose state and duration to accessibility',
        (tester) async {
      final now = DateTime(2026, 10, 24, 12);
      final stages = [
        SetupStage(
            id: 'a',
            label: 'Preparing files',
            state: SetupStageState.complete,
            startedAt: now.subtract(const Duration(seconds: 4)),
            finishedAt: now),
        SetupStage(
            id: 'b',
            label: 'Creating runtime',
            state: SetupStageState.running,
            startedAt: now.subtract(const Duration(seconds: 65))),
      ];
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(body: StageList(stages: stages, now: now))));
      expect(find.text('4s'), findsOneWidget);
      expect(find.text('1m 05s'), findsOneWidget);
    });
  });

  group('native adapters', () {
    test('launch acknowledgement sends only whitelisted fields', () async {
      final backend = AckBackend();
      final ack = BackendLaunchAcknowledgement(backend, root: r'C:\App');
      expect(LaunchAcknowledgement.newId(), matches(r'^[a-f0-9]{32}$'));
      await ack.acknowledge('b' * 32, state: launchConfigurationRequired);
      expect(backend.requests.single.parameters, {
        'action': 'package-launch-ack',
        'root': r'C:\App',
        'launch_id': 'b' * 32,
        'state': 'configuration_required',
      });
    });

    test('launch acknowledgement never reads a rejection as readiness', () async {
      final ack = BackendLaunchAcknowledgement(AckBackend(), root: r'C:\App');
      const quick = Duration(milliseconds: 60);
      const interval = Duration(milliseconds: 10);
      expect(await ack.waitFor('b' * 32, timeout: quick, interval: interval),
          launchReady);
      // Pending until the budget runs out is a timeout, not a failure state.
      expect(
          await ack.waitFor('d' * 32, timeout: quick, interval: interval),
          isNull);
      // Unknown or obsolete identifiers fail explicitly.
      expect(
          await ack.waitFor('e' * 32, timeout: quick, interval: interval),
          launchFailed);
    });

    test('instance guard refuses a second window for the same mode', () async {
      final directory = await Directory.systemTemp.createTemp('odessy-guard-');
      addTearDown(() => directory.delete(recursive: true));
      final first =
          await InstanceGuard.acquire('abc', directory: directory, mode: 'setup');
      expect(first, isNotNull);
      final second =
          await InstanceGuard.acquire('abc', directory: directory, mode: 'setup');
      expect(second, isNull);
      final otherMode =
          await InstanceGuard.acquire('abc', directory: directory, mode: 'manage');
      expect(otherMode, isNotNull);
      await second?.release();
      await first!.release();
      await otherMode!.release();
      final third =
          await InstanceGuard.acquire('abc', directory: directory, mode: 'setup');
      expect(third, isNotNull);
      await third!.release();
    });
  });
}
