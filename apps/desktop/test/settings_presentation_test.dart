import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/settings/presentation/settings.dart';
import 'component_settings_test.dart' show SavedComponents;

/// Explicit test and screenshot fixture; never used as a live fallback.
class SettingsFixtureApi extends DemoApi implements ConsultationLimitApi {
  SettingsFixtureApi({this.demo = true, int limit = 1}) {
    document = {
      'settings': {
        'max_consultations': limit,
        'enabled': false,
        'unrelated': 'preserve me'
      },
      'participants': {
        'existing': {'profile': 'local'}
      }
    };
  }
  final bool demo;
  late Json document;
  bool failRead = false, failWrite = false;
  int reads = 0, writes = 0;
  int? authoritativeLimit;
  Completer<Json>? readGate;
  Completer<void>? writeGate;
  int revision = 0;
  @override
  bool get isDemo => demo;
  @override
  String get identity => 'http://127.0.0.1:4321';
  @override
  Future<Json> settings() async {
    reads++;
    if (failRead) throw const ApiFailure(503);
    return readGate == null ? document : await readGate!.future;
  }

  @override
  Future<Json> saveSettings(Json value) async {
    writes++;
    await writeGate?.future;
    if (failWrite) throw const ApiFailure(500);
    document = {
      ...value,
      'settings': {
        ...value['settings'] as Map,
        if (authoritativeLimit != null) 'max_consultations': authoritativeLimit
      }
    };
    return document;
  }

  @override
  Future<Json> readConsultationLimit() async {
    final current = await settings();
    return {
      'instance_id': 'fixture',
      'revision': revision,
      'max_consultations': (current['settings'] as Map)['max_consultations']
    };
  }

  @override
  Future<Json> saveConsultationLimit(Json update) async {
    if (update['instance_id'] != 'fixture' || update['revision'] != revision) {
      throw const ApiFailure(409);
    }
    final saved = await saveSettings({
      ...document,
      'settings': {
        ...document['settings'] as Map,
        'max_consultations': update['max_consultations']
      }
    });
    revision++;
    return {
      'instance_id': 'fixture',
      'revision': revision,
      'max_consultations': (saved['settings'] as Map)['max_consultations']
    };
  }
}

class OfflineProfilesApi extends SettingsFixtureApi {
  @override
  Future<List<String>> profiles() async => throw const ApiFailure(503);
}

Future<void> mountSettings(WidgetTester tester, DesktopSession c,
    {Size size = const Size(1200, 1100),
    double scale = 1,
    Brightness brightness = Brightness.dark,
    bool installed = false,
    VoidCallback? model,
    VoidCallback? voice,
    VoidCallback? devices,
    VoidCallback? management,
    VoidCallback? toggleTheme,
    bool settle = true}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(MaterialApp(
    debugShowCheckedModeBanner: false,
    theme: odessyTheme(brightness),
    home: MediaQuery(
        data: MediaQueryData(size: size, textScaler: TextScaler.linear(scale)),
        child: Scaffold(
            body: SettingsPage(
                controller: c,
                installed: installed,
                onOpenGetStarted: model,
                onOpenVoice: voice,
                onOpenDevices: devices,
                onOpenManagement: management,
                onToggleTheme: toggleTheme))),
  ));
  if (settle) {
    await tester.pumpAndSettle();
  } else {
    await tester.pump();
  }
}

Future<void> selectSettingsSection(WidgetTester tester, String name,
    {bool settle = true}) async {
  final rail = find.byKey(ValueKey('settings-section-$name'));
  if (rail.evaluate().isNotEmpty) {
    await tester.ensureVisible(rail);
    await tester.tap(rail);
  } else {
    final picker = find.byKey(const Key('settings-section-picker'));
    await tester.ensureVisible(picker);
    await tester.tap(picker);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text(name).last);
    await tester.tap(find.text(name).last);
  }
  if (settle) {
    await tester.pumpAndSettle();
  } else {
    await tester.pump();
  }
}

Finder get limitField => find.byKey(const Key('consultation-limit'));
Finder get saveSettingsButton =>
    find.widgetWithText(FilledButton, 'Save settings');

Future<void> editLimit(WidgetTester tester, String value) async {
  await tester.ensureVisible(limitField);
  await tester.enterText(limitField, value);
  await tester.pump();
}

void main() {
  testWidgets('model setup shortcuts only navigate and expose checks',
      (tester) async {
    tester.view.physicalSize = const Size(1200, 1100);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = SettingsFixtureApi();
    final backend = SavedComponents();
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    await tester.pumpWidget(MaterialApp(
        theme: odessyTheme(Brightness.dark),
        home: Scaffold(
            body: SettingsPage(
                controller: c,
                configurationBackend: backend,
                initialSection: SettingsSections.models))));
    await tester.pumpAndSettle();
    expect(find.text('Set up a model'), findsOneWidget);
    expect(
        find.text(
            'A connected coordinator does not confirm that the selected model is available.'),
        findsOneWidget);
    expect(backend.requests.where((r) => r.operation == 'component-probe'),
        isEmpty);
    await tester.tap(find.text('Model checks'));
    await tester.pumpAndSettle();
    expect(find.text('Test model').hitTestable(), findsOneWidget);
    expect(backend.requests.where((r) => r.operation == 'component-probe'),
        isEmpty);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('models shows the current session profile and updates selection',
      (tester) async {
    final api = SettingsFixtureApi();
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    await mountSettings(tester, c);
    await selectSettingsSection(tester, SettingsSections.models);
    expect(find.text('Session profiles'), findsOneWidget);
    expect(find.text('auto'), findsOneWidget);
    expect(find.text('auto\nlocal\ncloud-example'), findsOneWidget);
    expect(find.text('These profiles belong to the demo session.'),
        findsOneWidget);
    await c.select('local');
    await tester.pumpAndSettle();
    expect(find.text('local'), findsOneWidget);
    expect(api.writes, 0);
  });

  testWidgets('offline model settings do not display cached profiles',
      (tester) async {
    final c = DesktopSession(OfflineProfilesApi());
    addTearDown(c.dispose);
    c.profiles = ['stale-profile'];
    await c.start();
    await mountSettings(tester, c);
    await selectSettingsSection(tester, SettingsSections.models);
    expect(find.text('2. Model checks are unavailable for this connection.'),
        findsOneWidget);
    expect(find.text('stale-profile'), findsNothing);
    expect(find.text('Unavailable'), findsNWidgets(2));
  });

  testWidgets('service connection does not imply a managed installation',
      (tester) async {
    final api = SettingsFixtureApi(demo: false);
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    await mountSettings(tester, c);
    expect(find.text('Preferences'), findsOneWidget);
    expect(find.text('Connected'), findsOneWidget);
    expect(find.text('Not managed by this app'), findsOneWidget);
    expect(find.text('Installed'), findsNothing);
    await selectSettingsSection(tester, SettingsSections.models);
    expect(find.text('Not reported'), findsOneWidget);
    expect(
        tester
            .widget<OutlinedButton>(
                find.widgetWithText(OutlinedButton, 'Configure a model'))
            .onPressed,
        isNull);
    expect(api.writes, 0);
  });

  testWidgets(
      'typed validation blocks invalid values and save preserves unrelated fields',
      (tester) async {
    final api = SettingsFixtureApi(demo: false);
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await mountSettings(tester, c);
    await selectSettingsSection(tester, SettingsSections.models);
    expect(tester.widget<FilledButton>(saveSettingsButton).onPressed, isNull);
    for (final value in ['-1', '33', 'text', '']) {
      await editLimit(tester, value);
      expect(tester.widget<TextField>(limitField).decoration!.errorText,
          isNotNull);
      expect(tester.widget<FilledButton>(saveSettingsButton).onPressed, isNull);
    }
    await editLimit(tester, '4');
    await tester.ensureVisible(saveSettingsButton);
    await tester.tap(saveSettingsButton);
    await tester.pumpAndSettle();
    expect(api.writes, 1);
    expect((api.document['settings'] as Map)['max_consultations'], 4);
    expect((api.document['settings'] as Map)['enabled'], false);
    expect((api.document['settings'] as Map)['unrelated'], 'preserve me');
    expect(api.document['participants'], {
      'existing': {'profile': 'local'}
    });
    expect(
        find.text('Saved by the service. Applies to newly created sessions.'),
        findsOneWidget);
    expect(tester.widget<FilledButton>(saveSettingsButton).onPressed, isNull);
  });

  testWidgets('success displays authoritative reload and labels demo memory',
      (tester) async {
    final api = SettingsFixtureApi()..authoritativeLimit = 7;
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await mountSettings(tester, c);
    await selectSettingsSection(tester, SettingsSections.models);
    await editLimit(tester, '4');
    await tester.ensureVisible(saveSettingsButton);
    await tester.tap(saveSettingsButton);
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(limitField).controller!.text, '7');
    expect(find.text('Saved in demo memory only.'), findsOneWidget);
    expect(api.reads, 3);
  });

  testWidgets('read failure disables editing and explicit reload recovers',
      (tester) async {
    final api = SettingsFixtureApi()..failRead = true;
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await mountSettings(tester, c);
    await selectSettingsSection(tester, SettingsSections.models);
    expect(tester.widget<TextField>(limitField).enabled, false);
    expect(find.textContaining('Could not load settings'), findsOneWidget);
    api.failRead = false;
    final reload = find.widgetWithText(OutlinedButton, 'Reload');
    await tester.ensureVisible(reload);
    await tester.tap(reload);
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(limitField).controller!.text, '1');
    expect(tester.widget<TextField>(limitField).enabled, true);
    expect(api.writes, 0);
  });

  testWidgets('loading and saving prevent duplicate edits and writes',
      (tester) async {
    final api = SettingsFixtureApi()..readGate = Completer<Json>();
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await mountSettings(tester, c, settle: false);
    await selectSettingsSection(tester, SettingsSections.models, settle: false);
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
    expect(tester.widget<TextField>(limitField).enabled, false);
    api.readGate!.complete(api.document);
    api.readGate = null;
    await tester.pumpAndSettle();
    await editLimit(tester, '2');
    api.writeGate = Completer<void>();
    await tester.ensureVisible(saveSettingsButton);
    await tester.tap(saveSettingsButton);
    await tester.pump();
    expect(api.writes, 1);
    expect(tester.widget<FilledButton>(saveSettingsButton).onPressed, isNull);
    expect(tester.widget<TextField>(limitField).enabled, false);
    api.writeGate!.complete();
    await tester.pumpAndSettle();
    expect(api.writes, 1);
  });

  testWidgets('save failure retains draft and reload requires explicit discard',
      (tester) async {
    final api = SettingsFixtureApi()..failWrite = true;
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await mountSettings(tester, c);
    await selectSettingsSection(tester, SettingsSections.models);
    await editLimit(tester, '4');
    await tester.ensureVisible(saveSettingsButton);
    await tester.tap(saveSettingsButton);
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(limitField).controller!.text, '4');
    expect(find.textContaining('draft retained'), findsOneWidget);
    expect(find.text('Saved in demo memory only.'), findsNothing);
    final reload = find.widgetWithText(OutlinedButton, 'Reload');
    await tester.tap(reload);
    await tester.pumpAndSettle();
    expect(find.text('Discard unsaved changes?'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(limitField).controller!.text, '4');
    await tester.tap(reload);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Discard changes'));
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(limitField).controller!.text, '1');
    expect(api.writes, 1);
  });

  testWidgets('service version conflict retains draft and requires reload',
      (tester) async {
    final api = SettingsFixtureApi(demo: false);
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await mountSettings(tester, c);
    await selectSettingsSection(tester, SettingsSections.models);
    await editLimit(tester, '4');
    api.revision++;
    await tester.ensureVisible(saveSettingsButton);
    await tester.tap(saveSettingsButton);
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(limitField).controller!.text, '4');
    expect(find.textContaining('settings changed'), findsOneWidget);
    expect(tester.widget<FilledButton>(saveSettingsButton).onPressed, isNull);
    expect(api.writes, 0);
  });

  testWidgets(
      'category changes preserve draft and review returns to its editor',
      (tester) async {
    final api = SettingsFixtureApi();
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await mountSettings(tester, c);
    await selectSettingsSection(tester, SettingsSections.models);
    await editLimit(tester, '5');
    await selectSettingsSection(tester, SettingsSections.general);
    expect(find.text('Unsaved collaboration changes'), findsOneWidget);
    await tester.tap(find.text('Review changes'));
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(limitField).controller!.text, '5');
    expect(api.writes, 0);
  });

  testWidgets('configuration entries use only their existing callbacks',
      (tester) async {
    final api = SettingsFixtureApi();
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    var models = 0, voice = 0, devices = 0, management = 0, themes = 0;
    await mountSettings(tester, c,
        installed: true,
        model: () => models++,
        voice: () => voice++,
        devices: () => devices++,
        management: () => management++,
        toggleTheme: () => themes++);
    await tester.tap(find.text('Use light theme'));
    await selectSettingsSection(tester, SettingsSections.models);
    await tester.ensureVisible(find.text('Configure a model'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Configure a model'));
    await selectSettingsSection(tester, SettingsSections.voice);
    await tester.tap(find.text('Open configuration'));
    await selectSettingsSection(tester, SettingsSections.devices);
    await tester.tap(find.text('Open devices'));
    await selectSettingsSection(tester, SettingsSections.installation);
    await tester.tap(find.text('Manage installation'));
    await tester.pumpAndSettle();
    expect([models, voice, devices, management, themes], [1, 1, 1, 1, 1]);
    expect(api.writes, 0);
  });

  testWidgets(
      'voice-only callback works without a model configuration callback',
      (tester) async {
    final c = DesktopSession(SettingsFixtureApi());
    addTearDown(c.dispose);
    var opened = 0;
    await mountSettings(tester, c, voice: () => opened++);
    await selectSettingsSection(tester, SettingsSections.voice);
    await tester.tap(find.text('Open configuration'));
    await tester.pumpAndSettle();
    expect(opened, 1);
  });

  testWidgets('leaving a dirty editor waits for discard or cancellation',
      (tester) async {
    final api = SettingsFixtureApi();
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    var opened = 0;
    await mountSettings(tester, c, model: () => opened++);
    await selectSettingsSection(tester, SettingsSections.models);
    await editLimit(tester, '6');
    await tester.tap(find.text('Configure a model'));
    await tester.pumpAndSettle();
    expect(opened, 0);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(limitField).controller!.text, '6');
    await tester.tap(find.text('Configure a model'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Discard changes'));
    await tester.pumpAndSettle();
    expect(opened, 1);
    expect(api.writes, 0);
  });

  testWidgets('connection replacement cannot write a previous service draft',
      (tester) async {
    final first = SettingsFixtureApi(), second = SettingsFixtureApi(limit: 9);
    final c = DesktopSession(first);
    addTearDown(c.dispose);
    await c.start();
    await mountSettings(tester, c);
    await selectSettingsSection(tester, SettingsSections.models);
    await editLimit(tester, '4');
    await tester.runAsync(() => c.replace(second));
    await tester.pumpAndSettle();
    expect(find.textContaining('Connection changed. Reload'), findsOneWidget);
    expect(tester.widget<FilledButton>(saveSettingsButton).onPressed, isNull);
    expect(tester.widget<TextField>(limitField).enabled, false);
    await tester.tap(find.text('Reload settings'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Discard changes'));
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(limitField).controller!.text, '9');
    expect(first.writes + second.writes, 0);
  });

  testWidgets('settings categories are reachable by keyboard', (tester) async {
    final c = DesktopSession(SettingsFixtureApi());
    addTearDown(c.dispose);
    await mountSettings(tester, c);
    for (var step = 0; step < 6 && limitField.evaluate().isEmpty; step++) {
      await tester.sendKeyEvent(LogicalKeyboardKey.tab);
      await tester.sendKeyEvent(LogicalKeyboardKey.enter);
      await tester.pumpAndSettle();
    }
    expect(limitField, findsOneWidget);
  });

  for (final brightness in Brightness.values) {
    testWidgets('all categories fit narrow large-text $brightness',
        (tester) async {
      final c = DesktopSession(SettingsFixtureApi());
      addTearDown(c.dispose);
      await mountSettings(tester, c,
          size: const Size(360, 640), scale: 2, brightness: brightness);
      for (final name in SettingsSections.all) {
        await selectSettingsSection(tester, name);
        expect(tester.takeException(), isNull, reason: name);
      }
    });
  }
}
