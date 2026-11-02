import 'dart:async';
import 'dart:ui' show AppExitResponse;
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/app/setup/installed_startup.dart';
import 'package:odessy_desktop/features/settings/presentation/settings.dart';
import 'settings_presentation_test.dart';
import 'installed_startup_test.dart' show Backend, ControlledStartup, specWith;
import 'component_settings_test.dart' show SavedComponents;

class _TrackedApi extends SettingsFixtureApi {
  bool closed = false;
  int deletes = 0;
  @override
  Future<void> deleteSession(String sid) async {
    deletes++;
    await super.deleteSession(sid);
  }

  @override
  void close() {
    closed = true;
    super.close();
  }
}

// Explicit installed-mode test fixture, with no network or native coordinator.
class _InstalledApi extends LocalApi {
  _InstalledApi(this.source)
      : super(Uri.parse('http://127.0.0.1:1'), 'fixture');
  final _TrackedApi source;
  @override
  bool get isDemo => true;
  @override
  Future<List<String>> profiles() => source.profiles();
  @override
  Future<Session> createSession() => source.createSession();
  @override
  Future<Session> snapshot(String sid) => source.snapshot(sid);
  @override
  Stream<SessionEvent> events(String sid, int after) =>
      source.events(sid, after);
  @override
  Future<Json> settings() => source.settings();
  @override
  Future<Json> saveSettings(Json value) => source.saveSettings(value);
  @override
  Future<void> deleteSession(String sid) => source.deleteSession(sid);
  @override
  void close() {
    source.close();
    super.close();
  }
}

class _Startup extends ControlledStartup {
  _Startup(super.backend);
  int shutdowns = 0;
  @override
  Future<void> shutdown() async {
    shutdowns++;
    await super.shutdown();
  }
}

class _InstalledBackend extends Backend {
  _InstalledBackend(super.spec);
  final components = SavedComponents();
  @override
  Future<SetupReply> execute(SetupRequest request) =>
      request.operation == 'component-settings'
          ? components.execute(request)
          : super.execute(request);
}

Future<void> _mount(WidgetTester tester, _TrackedApi api,
    {Size size = const Size(1400, 1300), Future<void> Function()? exit}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(OdessyApp(
      api: api, setupBackend: SavedComponents(), onExitApproved: exit));
  await tester.pumpAndSettle();
}

Future<void> _settings(WidgetTester tester) async {
  final drawer = find.byTooltip('Open navigation menu');
  if (drawer.evaluate().isNotEmpty) {
    await tester.tap(drawer);
    await tester.pumpAndSettle();
  }
  await tester.tap(find.widgetWithText(ListTile, 'Settings'));
  await tester.pumpAndSettle();
  if (drawer.evaluate().isNotEmpty) {
    final scaffold = tester.state<ScaffoldState>(find.byType(Scaffold).first);
    scaffold.closeDrawer();
    await tester.pumpAndSettle();
  }
  await selectSettingsSection(tester, SettingsSections.models);
}

Future<void> _answer(WidgetTester tester, String answer) async {
  if (find.text('Unsaved collaboration setting').evaluate().isNotEmpty) {
    if (answer == 'Cancel') answer = 'Keep editing';
    if (answer == 'Discard changes') answer = 'Discard';
  }
  await tester.tap(find.widgetWithText(TextButton, answer).evaluate().isNotEmpty
      ? find.widgetWithText(TextButton, answer)
      : find.widgetWithText(FilledButton, answer));
  await tester.pumpAndSettle();
}

Future<AppExitResponse> _finishExit(
    WidgetTester tester, Future<AppExitResponse> pending) async {
  AppExitResponse? result;
  pending.then((value) => result = value);
  // Resource cancellation schedules work after the dialog transition settles.
  // Keep pumping microtasks instead of awaiting with the fake clock suspended.
  for (var i = 0; result == null && i < 30; i++) {
    await tester
        .runAsync(() => Future<void>.delayed(const Duration(milliseconds: 10)));
    await tester.pump(const Duration(milliseconds: 10));
  }
  expect(result, isNotNull);
  return result!;
}

void main() {
  testWidgets('return to Assistant keeps the existing unsaved-draft guard',
      (tester) async {
    final api = _TrackedApi();
    await _mount(tester, api);
    await _settings(tester);
    await editLimit(tester, '4');
    final back = find.text('Return to Assistant');
    await tester.ensureVisible(back);
    await tester.tap(back);
    await tester.pumpAndSettle();
    expect(find.text('Unsaved collaboration setting'), findsOneWidget);
    await _answer(tester, 'Cancel');
    expect(find.text('Preferences'), findsOneWidget);
    expect(tester.widget<TextField>(limitField).controller!.text, '4');
    await tester.ensureVisible(back);
    await tester.tap(back);
    await tester.pumpAndSettle();
    await _answer(tester, 'Discard changes');
    expect(find.text('Preferences'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('pending initial read blocks departure until the form settles',
      (tester) async {
    final api = _TrackedApi();
    await _mount(tester, api);
    api.readGate = Completer<Json>();
    await tester.tap(find.widgetWithText(ListTile, 'Settings'));
    await tester.pump();
    await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
    await tester.pump();
    expect(find.text('Preferences'), findsOneWidget);
    expect(await tester.binding.handleRequestAppExit(), AppExitResponse.cancel);
    expect(api.closed, false);
    api.readGate!.complete(api.document);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
    await tester.pumpAndSettle();
    expect(find.text('Preferences'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('same-page navigation and theme changes preserve dirty draft',
      (tester) async {
    final api = _TrackedApi();
    await _mount(tester, api);
    await _settings(tester);
    await editLimit(tester, '4');
    await tester.tap(find.widgetWithText(ListTile, 'Settings'));
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    expect(find.text('Discard unsaved changes?'), findsNothing);
    expect(tester.widget<TextField>(limitField).controller!.text, '4');
    await tester.pumpWidget(const SizedBox());
  });
  for (final draft in ['4', '33']) {
    testWidgets('outer navigation protects $draft draft and explicit discard',
        (tester) async {
      final api = _TrackedApi();
      await _mount(tester, api);
      await _settings(tester);
      await editLimit(tester, draft);
      await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
      await tester.pumpAndSettle();
      expect(find.text('Unsaved collaboration setting'), findsOneWidget);
      await _answer(tester, 'Cancel');
      expect(tester.widget<TextField>(limitField).controller!.text, draft);
      expect(api.writes, 0);
      expect(api.closed, false);
      await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
      await tester.pumpAndSettle();
      await _answer(tester, 'Discard changes');
      expect(find.text('Preferences'), findsNothing);
      await _settings(tester);
      expect(tester.widget<TextField>(limitField).controller!.text, '1');
      await tester.pumpWidget(const SizedBox());
    });
  }
  testWidgets(
      'rejected device selection and settings-owned navigation confirm once',
      (tester) async {
    final api = _TrackedApi();
    await _mount(tester, api);
    await tester.tap(find.text('Jetson'));
    await tester.pumpAndSettle();
    await _settings(tester);
    await editLimit(tester, '4');
    await tester.tap(find.text('Laptop host'));
    await tester.pumpAndSettle();
    await _answer(tester, 'Cancel');
    await selectSettingsSection(tester, SettingsSections.devices);
    await tester.tap(find.text('Open devices'));
    await tester.pumpAndSettle();
    expect(find.text('Unsaved collaboration setting'), findsOneWidget);
    await _answer(tester, 'Discard changes');
    expect(find.text('Unsaved collaboration setting'), findsNothing);
    expect(find.text('Jetson'), findsOneWidget);
    expect(find.text('Preferences'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('connection cancellation preserves draft and old transport',
      (tester) async {
    final api = _TrackedApi();
    await _mount(tester, api);
    await _settings(tester);
    await editLimit(tester, '4');
    await tester.tap(find.byTooltip('Connect local service'));
    await tester.pumpAndSettle();
    await _answer(tester, 'Cancel');
    expect(find.text('Connect to Odessy'), findsNothing);
    await tester.tap(find.byTooltip('Connect local service'));
    await tester.pumpAndSettle();
    await _answer(tester, 'Discard changes');
    expect(find.text('Connect to Odessy'), findsOneWidget);
    await _answer(tester, 'Cancel');
    expect(tester.widget<TextField>(limitField).controller!.text, '4');
    expect(api.closed, false);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets(
      'pending save blocks navigation and exit; failed save keeps draft',
      (tester) async {
    final api = _TrackedApi();
    await _mount(tester, api);
    await _settings(tester);
    await editLimit(tester, '4');
    api.writeGate = Completer<void>();
    api.failWrite = true;
    await tester.tap(saveSettingsButton);
    await tester.pump();
    await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
    await tester.pump();
    expect(await tester.binding.handleRequestAppExit(), AppExitResponse.cancel);
    expect(api.closed, false);
    expect(api.deletes, 0);
    expect(find.text('Preferences'), findsOneWidget);
    api.writeGate!.complete();
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
    await tester.pumpAndSettle();
    await _answer(tester, 'Cancel');
    expect(tester.widget<TextField>(limitField).controller!.text, '4');
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('saved draft and unchanged form leave without confirmation',
      (tester) async {
    final api = _TrackedApi();
    await _mount(tester, api);
    await _settings(tester);
    await editLimit(tester, '4');
    await tester.tap(saveSettingsButton);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
    await tester.pumpAndSettle();
    expect(find.text('Discard unsaved changes?'), findsNothing);
    await _settings(tester);
    expect(tester.widget<TextField>(limitField).controller!.text, '4');
    await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
    await tester.pumpAndSettle();
    expect(find.text('Preferences'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets(
      'rapid requests share one decision and cancellation does not shutdown',
      (tester) async {
    var exits = 0;
    final api = _TrackedApi();
    await _mount(tester, api, exit: () async {
      exits++;
    });
    await _settings(tester);
    await editLimit(tester, '4');
    final settings = tester.widget<SettingsPage>(find.byType(SettingsPage));
    settings.onOpenDevices!();
    settings.onOpenDevices!();
    await tester.pumpAndSettle();
    expect(find.text('Unsaved collaboration setting'), findsOneWidget);
    expect(await tester.binding.handleRequestAppExit(), AppExitResponse.cancel);
    await _answer(tester, 'Cancel');
    final first = tester.binding.handleRequestAppExit();
    final second = tester.binding.handleRequestAppExit();
    await tester.pumpAndSettle();
    await _answer(tester, 'Cancel');
    expect(await first, AppExitResponse.cancel);
    expect(await second, AppExitResponse.cancel);
    expect(api.closed, false);
    expect(api.deletes, 0);
    expect(exits, 0);
    final accepted = tester.binding.handleRequestAppExit();
    await tester.pumpAndSettle();
    await _answer(tester, 'Discard changes');
    expect(await _finishExit(tester, accepted), AppExitResponse.exit);
    expect(api.closed, true);
    expect(api.deletes, 1);
    expect(exits, 1);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('narrow drawer cancellation retains settings draft',
      (tester) async {
    final api = _TrackedApi();
    await _mount(tester, api, size: const Size(720, 1100));
    await _settings(tester);
    await editLimit(tester, '4');
    await tester.tap(find.byTooltip('Open navigation menu'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
    await tester.pumpAndSettle();
    await _answer(tester, 'Cancel');
    final scaffold = tester.state<ScaffoldState>(find.byType(Scaffold).first);
    scaffold.closeDrawer();
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(limitField).controller!.text, '4');
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('installed exit cancellation does not stop startup or owned API',
      (tester) async {
    tester.view.physicalSize = const Size(1400, 1300);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = _TrackedApi();
    final installedApi = _InstalledApi(api);
    final backend = _InstalledBackend(specWith(model: true));
    final startup = _Startup(backend)
      ..spec = specWith(model: true)
      ..phase = InstalledPhase.ready
      ..api = installedApi;
    await tester.pumpWidget(InstalledApplication(
        installRoot: 'C:/Odessy/App',
        backend: backend,
        startupFactory: (_) => startup));
    await tester.pumpAndSettle();
    await _settings(tester);
    await editLimit(tester, '4');
    final exit = tester.binding.handleRequestAppExit();
    await tester.pumpAndSettle();
    await _answer(tester, 'Cancel');
    expect(await exit, AppExitResponse.cancel);
    expect(startup.shutdowns, 0);
    expect(backend.closed, false);
    expect(api.closed, false);
    final approved = tester.binding.handleRequestAppExit();
    await tester.pumpAndSettle();
    await _answer(tester, 'Discard changes');
    expect(await _finishExit(tester, approved), AppExitResponse.exit);
    expect(startup.shutdowns, 1);
    expect(backend.closed, true);
    expect(api.closed, true);
    await tester.pumpWidget(const SizedBox());
  });
}
