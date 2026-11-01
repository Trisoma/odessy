import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/setup/native_picker.dart';
import 'package:odessy_desktop/features/settings/controllers/component_settings_controller.dart';
import 'package:odessy_desktop/features/settings/controllers/voice_discovery_controller.dart';
import 'package:odessy_desktop/features/settings/presentation/component_settings_pane.dart';

/// Records every request so a test can prove what a section did *not* send.
class VoiceBackend implements SetupBackend {
  VoiceBackend({
    this.devices = const [],
    this.candidates = const {},
    this.issues = const {},
    this.missing = const [],
    this.partial = false,
    this.deviceError = false,
    this.discoveryError = false,
    Json? voice,
  }) : selection = {
          'role': 'combined',
          'model': null,
          'voice': <String, dynamic>{
            'enabled': false,
            'stt_provider': 'whisper.cpp',
            'tts_provider': 'kokoro',
            'whisper_bin': '/opt/whisper/whisper-cli',
            'whisper_model': '/opt/whisper/ggml-base.en.bin',
            'audio_backend': 'command',
            'audio_input_device': '',
            'audio_output_device': '',
            'wake_model': '',
            ...?voice,
          },
        };

  Json selection;
  final List<Json> devices;
  final Json candidates;
  final Json issues;
  final List<String> missing;
  final bool partial, deviceError, discoveryError;
  final requests = <SetupRequest>[];
  int revision = 0;

  Json get voice => selection['voice'] as Json;
  bool get savedAnything =>
      requests.any((r) => r.parameters['action'] == 'save');

  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    final p = request.parameters;
    if (request.operation == 'component-probe') {
      if (deviceError) {
        return _error(request, 'Enumeration failed.');
      }
      return _ok(request, {
        'section': 'voice',
        'backend': 'command',
        'status': 'available_unverified',
        'devices': devices,
        'directions': const {},
        'message': 'Device inventory only; no recording or playback performed.',
      });
    }
    if (request.operation == 'voice-discover') {
      if (discoveryError) return _error(request, 'Scan failed.');
      // check-only mode: verdicts, no directory walk.
      if (p['check'] == true) return _ok(request, {'issues': issues});
      return _ok(request, {
        'candidates': candidates,
        'issues': issues,
        'missing': missing,
        'partial': partial,
        'reason':
            'Existing files only; no inference, downloads or audio capture performed.',
      });
    }
    if (p['action'] == 'save') {
      voice.addAll(p['patch'] as Json);
      revision++;
    }
    return _ok(request, {
      'selection': selection,
      'revision': '$revision',
      'plan_id': 'review-$revision',
      'saved': p['action'] == 'save',
    });
  }

  SetupReply _ok(SetupRequest request, Json result) => SetupReply.fromJson(
      {'id': request.id, 'status': 'ok', 'result': result}, request.id);

  SetupReply _error(SetupRequest request, String reason) =>
      SetupReply.fromJson({
        'id': request.id,
        'status': 'error',
        'error': 'Setup failed.',
        'reason': reason
      }, request.id);

  @override
  Future<void> close() async {}

  @override
  Future<void> cancel() async {}
}

const headphones = <String, dynamic>{
  'id': 'hw:CARD=Headset,DEV=0',
  'name': 'USB Headset',
  'input': true,
  'output': true
};
const microphone = <String, dynamic>{
  'id': 'hw:CARD=Mic,DEV=0',
  'name': 'Desk Mic',
  'input': true,
  'output': false
};
const speakers = <String, dynamic>{
  'id': 'hw:CARD=HDMI,DEV=3',
  'name': 'HDMI Output',
  'input': false,
  'output': true,
  'is_default': true
};

/// A backend whose replies are completed by the test, in whatever order it likes.
class _OutOfOrderBackend implements SetupBackend {
  final pending = <Completer<Json>>[];
  final requests = <SetupRequest>[];

  @override
  Future<SetupReply> execute(SetupRequest request) {
    requests.add(request);
    final completer = Completer<Json>();
    pending.add(completer);
    return completer.future.then((result) => SetupReply.fromJson(
        {'id': request.id, 'status': 'ok', 'result': result}, request.id));
  }

  @override
  Future<void> close() async {}

  @override
  Future<void> cancel() async {}
}

/// A picker the user dismisses: every call returns null and changes nothing.
class _CancellingPicker implements NativePicker {
  const _CancellingPicker();
  @override
  Future<String?> pickDirectory({String? initial, String? title}) async => null;
  @override
  Future<String?> pickFile(
          {String? initial, List<String> extensions = const []}) async =>
      null;
  @override
  Future<String?> pickSaveLocation(
          {required String suggestedName,
          List<String> extensions = const []}) async =>
      null;
}

Future<ComponentSettingsController> open(
    WidgetTester tester, VoiceBackend backend, String section) async {
  tester.view.physicalSize = const Size(1200, 1400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final controller = ComponentSettingsController(backend);
  await tester.pumpWidget(MaterialApp(
      home: Scaffold(
          body: SingleChildScrollView(
              child: ComponentSettingsPane(
                  controller: controller, section: section)))));
  await tester.pumpAndSettle();
  return controller;
}

Future<void> save(WidgetTester tester) async {
  await tester.ensureVisible(find.text('Save this section'));
  await tester.tap(find.text('Save this section'));
  await tester.pumpAndSettle();
}

Json savedPatch(VoiceBackend backend) => backend.requests
    .firstWhere((r) => r.parameters['action'] == 'save')
    .parameters['patch'] as Json;

void main() {
  testWidgets('microphone and speaker are selectors with filtered capabilities',
      (tester) async {
    final backend = VoiceBackend(devices: [headphones, microphone, speakers]);
    final controller = await open(tester, backend, 'voice');

    expect(find.byKey(const ValueKey('component-audio_input_device')),
        findsOneWidget);
    expect(find.byKey(const ValueKey('component-audio_output_device')),
        findsOneWidget);
    expect(find.text('System default'), findsNWidgets(2));

    await tester
        .tap(find.byKey(const ValueKey('component-audio_input_device')));
    await tester.pumpAndSettle();
    expect(find.text('Desk Mic'), findsWidgets);
    expect(find.text('HDMI Output'), findsNothing,
        reason: 'an output-only device must not be offered as a microphone');
    await tester.tapAt(const Offset(4, 4));
    await tester.pumpAndSettle();

    await tester
        .tap(find.byKey(const ValueKey('component-audio_output_device')));
    await tester.pumpAndSettle();
    expect(find.text('HDMI Output · system default'), findsWidgets);
    expect(find.text('Desk Mic'), findsNothing,
        reason: 'an input-only device must not be offered as a speaker');
    await tester.tapAt(const Offset(4, 4));
    await tester.pumpAndSettle();
    controller.dispose();
  });

  testWidgets('choosing a microphone changes one identifier and saves nothing',
      (tester) async {
    final backend = VoiceBackend(devices: [headphones, microphone, speakers]);
    final controller = await open(tester, backend, 'voice');

    await tester
        .tap(find.byKey(const ValueKey('component-audio_input_device')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Desk Mic').last);
    await tester.pumpAndSettle();

    expect(backend.savedAnything, isFalse,
        reason: 'selecting a device is a draft edit, never a save');

    await save(tester);
    final patch = savedPatch(backend);
    expect(patch['audio_input_device'], 'hw:CARD=Mic,DEV=0');
    expect(patch['audio_output_device'], '',
        reason: 'the speaker keeps its own value');
    expect(patch['audio_backend'], 'command',
        reason: 'selecting a device must not switch the audio backend');
    expect(patch['enabled'], isFalse,
        reason: 'nothing here may enable the voice pipeline');
    expect(backend.selection['model'], isNull);
    controller.dispose();
  });

  testWidgets(
      'a saved device that disappeared is kept and labelled unavailable',
      (tester) async {
    final backend = VoiceBackend(
        devices: [microphone],
        voice: {'audio_input_device': 'hw:CARD=Gone,DEV=0'});
    final controller = await open(tester, backend, 'voice');

    expect(find.textContaining('Previously selected device — unavailable'),
        findsWidgets);
    expect(find.textContaining('hw:CARD=Gone,DEV=0'), findsWidgets);
    expect(backend.savedAnything, isFalse,
        reason: 'an unavailable device is never silently replaced');

    await save(tester);
    expect(backend.voice['audio_input_device'], 'hw:CARD=Gone,DEV=0');
    controller.dispose();
  });

  testWidgets('advanced exposes the raw identifier without losing it',
      (tester) async {
    final backend = VoiceBackend(
        devices: [microphone], voice: {'audio_input_device': 'custom-device'});
    final controller = await open(tester, backend, 'voice');

    await tester.tap(find.byKey(const ValueKey('component-custom-device')));
    await tester.pumpAndSettle();

    final field = tester.widget<TextField>(
        find.byKey(const ValueKey('component-audio_input_device')));
    expect(field.controller!.text, 'custom-device');
    await tester.enterText(
        find.byKey(const ValueKey('component-audio_input_device')), 'pulse');
    await tester.pumpAndSettle();
    await save(tester);
    expect(backend.voice['audio_input_device'], 'pulse');
    controller.dispose();
  });

  testWidgets('device detection is observational, never a test run',
      (tester) async {
    final backend = VoiceBackend(devices: [microphone]);
    final controller = await open(tester, backend, 'voice');
    await tester.tap(find.text('Refresh devices'));
    await tester.pumpAndSettle();

    final probes =
        backend.requests.where((r) => r.operation == 'component-probe');
    expect(probes, isNotEmpty);
    for (final probe in probes) {
      expect(probe.parameters['run'], isFalse,
          reason: 'detection must never record, play or call a provider');
      expect(probe.parameters['section'], 'voice');
    }
    controller.dispose();
  });

  testWidgets('detected files are offered without editing the draft',
      (tester) async {
    final backend = VoiceBackend(candidates: const {
      'whisper_model': ['/opt/whisper/ggml-large.bin']
    });
    final controller = await open(tester, backend, 'stt');

    expect(find.text('Detected (1)'), findsOneWidget);
    final field = tester.widget<TextField>(
        find.byKey(const ValueKey('component-whisper_model')));
    expect(field.controller!.text, '/opt/whisper/ggml-base.en.bin',
        reason: 'suggestions must not change the draft');
    expect(backend.savedAnything, isFalse);

    await tester.tap(find.text('Detected (1)'));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('ggml-large.bin').last);
    await tester.pumpAndSettle();

    await save(tester);
    final patch = savedPatch(backend);
    expect(patch['whisper_model'], '/opt/whisper/ggml-large.bin');
    expect(patch['whisper_bin'], '/opt/whisper/whisper-cli',
        reason: 'choosing a model must not touch the executable');
    controller.dispose();
  });

  testWidgets('a failed scan leaves the section editable and saveable',
      (tester) async {
    final backend = VoiceBackend(discoveryError: true, deviceError: true);
    final controller = await open(tester, backend, 'voice');

    expect(find.textContaining('Enumeration failed.'), findsOneWidget);
    expect(find.byKey(const ValueKey('component-wake_model')), findsOneWidget);
    await tester.enterText(
        find.byKey(const ValueKey('component-wake_model')), '/models/hey.onnx');
    await tester.pumpAndSettle();
    await save(tester);
    expect(backend.voice['wake_model'], '/models/hey.onnx');
    controller.dispose();
  });

  testWidgets('a partial scan says so and still offers Browse', (tester) async {
    final backend = VoiceBackend(partial: true, candidates: const {
      'whisper_model': ['/models/ggml-tiny.bin']
    });
    final controller = await open(tester, backend, 'stt');
    expect(find.textContaining('Scan stopped early'), findsOneWidget);
    expect(find.text('Browse…'), findsWidgets);
    controller.dispose();
  });

  testWidgets('opening a section only loads; it never writes', (tester) async {
    final backend = VoiceBackend(devices: [
      microphone
    ], candidates: const {
      'wake_model': ['/m/hey.onnx']
    });
    final controller = await open(tester, backend, 'voice');
    expect(backend.savedAnything, isFalse);
    for (final request in backend.requests) {
      if (request.operation != 'component-settings') continue;
      expect(request.parameters['action'], 'load');
    }
    controller.dispose();
  });

  testWidgets('cancelling Browse changes nothing', (tester) async {
    final backend = VoiceBackend();
    final controller = ComponentSettingsController(backend);
    tester.view.physicalSize = const Size(1200, 1400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: ComponentSettingsPane(
                    controller: controller,
                    section: 'stt',
                    picker: const _CancellingPicker())))));
    await tester.pumpAndSettle();

    final before = tester
        .widget<TextField>(find.byKey(const ValueKey('component-whisper_bin')))
        .controller!
        .text;
    await tester.tap(find.text('Browse…').first);
    await tester.pumpAndSettle();

    expect(
        tester
            .widget<TextField>(
                find.byKey(const ValueKey('component-whisper_bin')))
            .controller!
            .text,
        before);
    expect(backend.requests.where((r) => r.parameters['action'] == 'save'),
        isEmpty);
    controller.dispose();
  });

  testWidgets(
      'a blocking verdict on another component never stops this section',
      (tester) async {
    // Whisper is unusable, the TTS files are fine. Saving TTS must still work.
    final backend = VoiceBackend(issues: const {
      'whisper_bin': {
        'message': 'Select the Whisper executable, not a folder.',
        'blocking': true
      }
    }, voice: {
      'kokoro_model': '/opt/kokoro/kokoro.onnx',
      'kokoro_voices': '/opt/kokoro/voices.bin',
    });
    final controller = await open(tester, backend, 'tts');

    expect(
        find.text('Select the Whisper executable, not a folder.'), findsNothing,
        reason: 'another component’s error must not appear in this section');

    await tester.enterText(find.byKey(const ValueKey('component-kokoro_model')),
        '/opt/kokoro/kokoro-v1.onnx');
    await tester.pumpAndSettle();
    await save(tester);

    expect(backend.savedAnything, isTrue,
        reason: 'a blocking STT verdict must not disable the TTS save');
    expect(backend.voice['kokoro_model'], '/opt/kokoro/kokoro-v1.onnx');
    controller.dispose();
  });

  testWidgets('a blocking verdict on the previous provider stops blocking',
      (tester) async {
    // The Kokoro files are unusable, but the section is now on a custom provider
    // whose own fields are fine, so the save must be allowed.
    final backend = VoiceBackend(issues: const {
      'kokoro_model': {
        'message': 'Select a file, not a folder.',
        'blocking': true
      }
    });
    final controller = await open(tester, backend, 'tts');

    await tester.enterText(find.byKey(const ValueKey('component-tts_provider')),
        'trusted:factory');
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const ValueKey('component-tts_executable')), '/opt/tts/say');
    await tester.enterText(find.byKey(const ValueKey('component-tts_model')),
        '/opt/tts/model.bin');
    await tester.pumpAndSettle();
    await save(tester);

    expect(backend.savedAnything, isTrue,
        reason: 'the hidden provider’s field must not gate this provider');
    controller.dispose();
  });

  group('validation ordering', () {
    test('editing a field invalidates a pending verdict without another scan',
        () async {
      final backend = _OutOfOrderBackend();
      final controller = VoiceDiscoveryController(backend);
      final old = controller.validate(const {
        'voice': {'whisper_bin': '/old-folder'}
      });
      controller.selectionChanged(const {
        'voice': {'whisper_bin': '/new/whisper-cli'}
      });
      backend.pending.single.complete(const {
        'whisper_bin': {'message': 'stale folder error', 'blocking': true}
      });
      await old;
      expect(controller.issues, isEmpty);
      expect(backend.requests.length, 1);
      controller.dispose();
    });

    test('an older check cannot restore an error after the user fixed the path',
        () async {
      final backend = _OutOfOrderBackend();
      final controller = VoiceDiscoveryController(backend);

      final first = controller.validate(const {
        'voice': {'whisper_bin': '/broken'}
      });
      final second = controller.validate(const {
        'voice': {'whisper_bin': '/fixed/whisper-cli'}
      });

      // The newer, correct check answers first...
      backend.pending[1].complete(const {});
      await second;
      expect(controller.blocking.contains('whisper_bin'), isFalse);
      expect(controller.issues.containsKey('whisper_bin'), isFalse);

      // ...then the stale one arrives late and must be ignored.
      backend.pending[0].complete(const {
        'whisper_bin': {
          'message': 'Select an executable file, not a folder.',
          'blocking': true
        }
      });
      await first;
      expect(controller.issues.containsKey('whisper_bin'), isFalse,
          reason: 'a superseded check must not overwrite newer verdicts');
      controller.dispose();
    });

    test('an older scan cannot overwrite newer check verdicts', () async {
      final backend = _OutOfOrderBackend();
      final controller = VoiceDiscoveryController(backend);

      final scan = controller.detect(const {'voice': <String, dynamic>{}});
      final check = controller.validate(const {
        'voice': {'whisper_bin': '/fixed/whisper-cli'}
      });
      backend.pending[1].complete(const {});
      await check;
      backend.pending[0].complete(const {
        'candidates': <String, dynamic>{},
        'missing': <String>[],
        'partial': false,
        'reason': '',
        'issues': {
          'whisper_bin': {'message': 'stale', 'blocking': true}
        },
      });
      await scan;

      expect(controller.issues.containsKey('whisper_bin'), isFalse,
          reason: 'scan verdicts for the previous values must be dropped');
      controller.dispose();
    });
  });

  group('helper-reported path verdicts', () {
    testWidgets('a folder in the Whisper field blocks the save',
        (tester) async {
      final backend = VoiceBackend(issues: const {
        'whisper_bin': {
          'message': 'Select the Whisper executable, not a folder.',
          'blocking': true
        }
      });
      final controller = await open(tester, backend, 'stt');

      expect(find.text('Select the Whisper executable, not a folder.'),
          findsWidgets);

      await tester.enterText(
          find.byKey(const ValueKey('component-whisper_model')),
          '/opt/whisper/ggml-large.bin');
      await tester.pumpAndSettle();
      await save(tester);
      expect(backend.savedAnything, isFalse,
          reason: 'a blocking path verdict must stop the save');
      controller.dispose();
    });

    testWidgets('an advisory verdict is shown but does not block',
        (tester) async {
      final backend = VoiceBackend(issues: const {
        'whisper_bin': {
          'message': 'Not found on PATH or on disk. Saving is still allowed.',
          'blocking': false
        }
      });
      final controller = await open(tester, backend, 'stt');
      expect(find.textContaining('Saving is still allowed'), findsWidgets);

      await tester.enterText(
          find.byKey(const ValueKey('component-whisper_model')),
          '/opt/whisper/ggml-large.bin');
      await tester.pumpAndSettle();
      await save(tester);
      expect(backend.savedAnything, isTrue);
      controller.dispose();
    });

    testWidgets('submitting a path asks the helper to re-check it',
        (tester) async {
      final backend = VoiceBackend();
      final controller = await open(tester, backend, 'stt');
      await tester.enterText(
          find.byKey(const ValueKey('component-whisper_bin')),
          '/opt/whisper/whisper-cli');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();

      final checks = backend.requests.where((r) =>
          r.operation == 'voice-discover' && r.parameters['check'] == true);
      expect(checks, isNotEmpty,
          reason: 're-checking a path must not re-run the whole scan');
      controller.dispose();
    });
  });
}
