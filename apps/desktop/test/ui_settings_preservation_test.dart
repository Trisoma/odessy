import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/preferences/desktop_preferences.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/settings/controllers/desktop_preferences_controller.dart';
import 'package:odessy_desktop/features/settings/presentation/appearance_preferences.dart';
import 'package:odessy_desktop/features/settings/presentation/settings.dart';

import 'settings_presentation_test.dart' show SettingsFixtureApi;

class _Store implements DesktopPreferencesStore {
  _Store({this.snapshot = const PreferencesSnapshot(DesktopPreferences(), 0)});
  PreferencesSnapshot snapshot;
  Object? saveError;
  int saves = 0;

  @override
  Future<PreferencesSnapshot> load() async => snapshot;

  @override
  Future<PreferencesSnapshot> save(DesktopPreferences value,
      {required int revision}) async {
    saves++;
    if (saveError != null) throw saveError!;
    snapshot = PreferencesSnapshot(value, revision + 1);
    return snapshot;
  }
}

Future<void> _pumpAppearance(
    WidgetTester tester, DesktopPreferencesController controller,
    {Size size = const Size(1100, 1400)}) async {
  await tester.binding.setSurfaceSize(size);
  addTearDown(() => tester.binding.setSurfaceSize(null));
  await tester.pumpWidget(MaterialApp(
      theme: odessyTheme(Brightness.dark),
      home: Scaffold(
          body: SingleChildScrollView(
              child: AppearancePreferences(controller: controller)))));
  await tester.pumpAndSettle();
}

Future<DesktopSession> _session(WidgetTester tester) async {
  final session = DesktopSession(SettingsFixtureApi());
  addTearDown(session.dispose);
  await session.start();
  return session;
}

void main() {
  testWidgets('appearance edits are drafts until Save persists them',
      (tester) async {
    final store = _Store();
    final controller = DesktopPreferencesController(store);
    addTearDown(controller.dispose);
    await _pumpAppearance(tester, controller);

    expect(find.text('Appearance'), findsOneWidget);
    expect(find.byKey(const Key('ui-preferences-unsaved')), findsNothing);
    expect(store.saves, 0);

    await tester.tap(find.text('Light'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('ui-preferences-unsaved')), findsOneWidget);
    expect(store.saves, 0);
    expect(controller.value.theme, DesktopTheme.light);

    await tester.tap(find.byKey(const Key('ui-preferences-save')));
    await tester.pumpAndSettle();
    expect(store.saves, 1);
    expect(controller.saved.theme, DesktopTheme.light);
    expect(find.byKey(const Key('ui-preferences-unsaved')), findsNothing);
    expect(find.byKey(const Key('ui-preferences-status')), findsOneWidget);
  });

  testWidgets('conflict is reported and the draft is kept', (tester) async {
    final store = _Store();
    final controller = DesktopPreferencesController(store);
    addTearDown(controller.dispose);
    await _pumpAppearance(tester, controller);

    await tester.tap(find.text('Light'));
    await tester.pumpAndSettle();
    store.saveError = const PreferencesConflict();
    await tester.tap(find.byKey(const Key('ui-preferences-save')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('ui-preferences-error')), findsOneWidget);
    expect(find.byKey(const Key('ui-preferences-unsaved')), findsOneWidget);
    expect(controller.value.theme, DesktopTheme.light);
    expect(controller.conflict, isTrue);
  });

  testWidgets('reload discards a draft only after explicit confirmation',
      (tester) async {
    final store = _Store(
        snapshot: const PreferencesSnapshot(
            DesktopPreferences(theme: DesktopTheme.light), 2));
    final controller = DesktopPreferencesController(store);
    addTearDown(controller.dispose);
    await _pumpAppearance(tester, controller);

    await tester.tap(find.text('Dark'));
    await tester.pumpAndSettle();
    expect(controller.dirty, isTrue);

    await tester.tap(find.byKey(const Key('ui-preferences-reload')));
    await tester.pumpAndSettle();
    expect(find.text('Discard appearance changes?'), findsOneWidget);
    await tester.tap(find.text('Keep editing'));
    await tester.pumpAndSettle();
    expect(controller.dirty, isTrue);

    await tester.tap(find.byKey(const Key('ui-preferences-reload')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Reload'));
    await tester.pumpAndSettle();
    expect(controller.dirty, isFalse);
    expect(controller.value.theme, DesktopTheme.light);
    expect(store.saves, 0);
  });

  testWidgets('text scale and transparency controls stay bounded',
      (tester) async {
    final controller = DesktopPreferencesController(_Store());
    addTearDown(controller.dispose);
    await _pumpAppearance(tester, controller);

    final slider =
        tester.widget<Slider>(find.byKey(const Key('ui-preferences-scale')));
    expect(slider.min, 1.0);
    expect(slider.max, 1.6);
    expect(slider.divisions, 6);
    expect(find.text('Text size · 100%'), findsOneWidget);

    await tester.tap(find.byKey(const Key('ui-preferences-transparency')));
    await tester.pumpAndSettle();
    expect(controller.value.reduceTransparency, isTrue);
    expect(find.text('Text size · 100%'), findsOneWidget);
  });

  testWidgets(
      'settings keeps every inventoried section and the real appearance',
      (tester) async {
    final session = await _session(tester);
    final controller = DesktopPreferencesController(_Store());
    addTearDown(controller.dispose);
    await tester.binding.setSurfaceSize(const Size(1200, 1200));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(MaterialApp(
        theme: odessyTheme(Brightness.dark),
        home: Scaffold(
            body: SettingsPage(
                controller: session, preferencesController: controller))));
    await tester.pumpAndSettle();

    for (final section in SettingsSections.all) {
      expect(find.text(section), findsWidgets, reason: section);
    }
    expect(find.byKey(const Key('ui-preferences')), findsOneWidget);
    expect(find.byKey(const Key('ui-preferences-save')), findsOneWidget);

    Future<void> open(String section) async {
      await tester.tap(find.byKey(ValueKey('settings-section-$section')));
      await tester.pumpAndSettle();
    }

    await open(SettingsSections.models);
    expect(find.text('Set up a model'), findsOneWidget);
    expect(find.text('Saved profiles'), findsOneWidget);
    expect(find.text('Edit round limit'), findsOneWidget);

    await open(SettingsSections.stt);
    expect(find.byKey(const Key('ui-preferences')), findsNothing);
    await open(SettingsSections.tts);
    await open(SettingsSections.voice);
    expect(find.textContaining('Voice'), findsWidgets);
    await open(SettingsSections.devices);
    expect(find.text('Devices and tool access'), findsWidgets);
    await open(SettingsSections.advanced);
    expect(find.text('Advanced'), findsWidgets);
    await open(SettingsSections.installation);
    expect(find.text('Installed copy'), findsOneWidget);
    expect(
        find.textContaining('Repair, install a newer local package'),
        findsOneWidget);

    await open(SettingsSections.general);
    expect(find.byKey(const Key('ui-preferences')), findsOneWidget);
  });

  testWidgets('an unsaved appearance draft blocks section changes',
      (tester) async {
    final session = await _session(tester);
    final controller = DesktopPreferencesController(_Store());
    addTearDown(controller.dispose);
    await tester.binding.setSurfaceSize(const Size(1200, 1200));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(MaterialApp(
        theme: odessyTheme(Brightness.dark),
        home: Scaffold(
            body: SettingsPage(
                controller: session, preferencesController: controller))));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Light'));
    await tester.pumpAndSettle();
    expect(controller.dirty, isTrue);

    await tester.tap(find
        .byKey(const ValueKey('settings-section-${SettingsSections.models}')));
    await tester.pumpAndSettle();
    expect(find.text('Discard appearance changes?'), findsOneWidget);
    await tester.tap(find.text('Keep editing'));
    await tester.pumpAndSettle();
    expect(find.text('Set up a model'), findsNothing);
    expect(controller.dirty, isTrue);

    await tester.tap(find
        .byKey(const ValueKey('settings-section-${SettingsSections.models}')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Discard changes'));
    await tester.pumpAndSettle();
    expect(controller.dirty, isFalse);
    expect(find.text('Set up a model'), findsOneWidget);
  });

  testWidgets('narrow settings keeps a section picker with all eight sections',
      (tester) async {
    final session = await _session(tester);
    final controller = DesktopPreferencesController(_Store());
    addTearDown(controller.dispose);
    await tester.binding.setSurfaceSize(const Size(430, 1100));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(MaterialApp(
        theme: odessyTheme(Brightness.dark),
        home: Scaffold(
            body: SettingsPage(
                controller: session, preferencesController: controller))));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('settings-section-picker')), findsOneWidget);
    await tester.tap(find.byKey(const Key('settings-section-picker')));
    await tester.pumpAndSettle();
    for (final section in SettingsSections.all) {
      expect(find.text(section), findsWidgets, reason: section);
    }
    expect(find.byKey(const Key('ui-preferences')), findsOneWidget);
  });
}
