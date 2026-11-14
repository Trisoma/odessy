import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
import 'tastecode_wired_test.dart' show WiredCaptureApi, CaptureProjects;
import 'component_settings_test.dart' show SavedComponents;
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/features/conversation/controllers/voice_controller.dart';
import 'package:odessy_desktop/features/shell/presentation/app_icon_rail.dart';
import 'package:odessy_desktop/features/shell/presentation/device_sidebar.dart';
import 'package:odessy_desktop/features/workspaces/presentation/workspace_inspector.dart';
import 'voice_controller_test.dart' show AudioApi;
import 'package:odessy_desktop/app/preferences/shell_layout.dart';
import 'package:odessy_desktop/app/preferences/desktop_preferences.dart';

class DelayedLayoutStore implements ShellLayoutStore {
  final ready = Completer<ShellLayoutSnapshot>();
  ShellLayoutSnapshot? saved;
  @override
  Future<ShellLayoutSnapshot> load() => ready.future;
  @override
  Future<ShellLayoutSnapshot> save(ShellLayout layout,
      {required int revision}) async {
    expect(revision, 7);
    return saved = ShellLayoutSnapshot(layout, revision + 1);
  }
}

class ScaledPreferences implements DesktopPreferencesStore {
  ScaledPreferences(this.scale);
  final double scale;
  @override
  Future<PreferencesSnapshot> load() async =>
      PreferencesSnapshot(DesktopPreferences(textScale: scale), 0);
  @override
  Future<PreferencesSnapshot> save(DesktopPreferences value,
          {required int revision}) async =>
      PreferencesSnapshot(value, revision + 1);
}

void main() {
  testWidgets(
      'late preference load cannot undo user sidebar edit or save stale revision',
      (tester) async {
    tester.view.physicalSize = const Size(1600, 1050);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final store = DelayedLayoutStore();
    await tester.pumpWidget(OdessyApp(
        api: WiredCaptureApi(),
        projectStore: CaptureProjects(),
        setupBackend: SavedComponents(),
        shellLayoutStore: store));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Collapse sidebar'));
    await tester.pumpAndSettle();
    store.ready.complete(const ShellLayoutSnapshot(ShellLayout(), 7));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('project-chat-sidebar')), findsNothing);
    expect(store.saved?.layout.sidebarCollapsed, isTrue);
    expect(tester.takeException(), isNull);
  });
  testWidgets(
      'production shell separates icon rail and sidebar and toggles inspector',
      (tester) async {
    tester.view.physicalSize = const Size(1600, 1050);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(OdessyApp(
        api: WiredCaptureApi(),
        projectStore: CaptureProjects(),
        setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('app-icon-rail')), findsOneWidget);
    expect(tester.getSize(find.byKey(const Key('app-icon-rail'))).width, 56);
    expect(find.byKey(const Key('project-chat-sidebar')), findsOneWidget);
    await tester.tap(find.byTooltip('Collapse sidebar'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('project-chat-sidebar')), findsNothing);
    expect(find.byKey(const Key('app-icon-rail')), findsOneWidget);
    await tester.tap(find.byTooltip('Open workspace inspector'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('inspector-resize-handle')), findsOneWidget);
    final before = tester.getSize(find.byType(WorkspaceInspector)).width;
    await tester.drag(
        find.byKey(const Key('inspector-resize-handle')), const Offset(-60, 0));
    await tester.pumpAndSettle();
    expect(tester.getSize(find.byType(WorkspaceInspector)).width,
        greaterThan(before));
    await tester.tap(find.byTooltip('Close workspace inspector'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('inspector-resize-handle')), findsNothing);
    expect(find.byType(ConversationPage), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'voice popover dispatches real controller commands and access seam stays honest',
      (tester) async {
    final api = AudioApi();
    final session = DesktopSession(api);
    await session.start();
    final voice = VoiceController(session);
    await voice.refresh();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ConversationPage(controller: session, voice: voice))));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Voice options'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Test speech output'));
    await tester.pumpAndSettle();
    expect(api.commands, contains('test_output'));
    await tester.tap(find.byTooltip('Service permissions'));
    await tester.pumpAndSettle();
    expect(find.text('Full control'), findsOneWidget);
    expect(find.text('Service mode control is not available yet.'),
        findsOneWidget);
    expect(
        tester
            .widget<PopupMenuItem<AccessMode>>(find.ancestor(
                of: find.text('Full control'),
                matching: find.byType(PopupMenuItem<AccessMode>)))
            .enabled,
        isFalse);
    await tester.tapAt(const Offset(5, 5));
    await tester.pumpAndSettle();
    await tester.pumpWidget(const SizedBox.shrink());
    voice.dispose();
    session.dispose();
  });

  testWidgets(
      'icon rail names destinations and keyboard activates focused command',
      (tester) async {
    var selected = false;
    final focus = FocusNode();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: AppIconRail(
                navigationFocus: {'Jobs': focus},
                onSettings: () {},
                destinations: [
                  SidebarDestination(
                      label: 'Jobs',
                      icon: Icons.work_outline,
                      onTap: () => selected = true),
                ]))));
    focus.requestFocus();
    await tester.pump();
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pump();
    expect(selected, isTrue);
    expect(find.byTooltip('Jobs'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    focus.dispose();
  });

  testWidgets(
      'saved inspector preference reopens from the live owner and persists closes',
      (tester) async {
    tester.view.physicalSize = const Size(1600, 1050);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final store = MemoryShellLayoutStore(
        const ShellLayout(inspectorOpen: true, inspectorWidth: 420));
    await tester.pumpWidget(OdessyApp(
        api: WiredCaptureApi(),
        projectStore: CaptureProjects(),
        setupBackend: SavedComponents(),
        shellLayoutStore: store));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('inspector-resize-handle')), findsOneWidget);
    expect(tester.getSize(find.byType(WorkspaceInspector)).width, 420);
    await tester.tap(find.byTooltip('Close workspace inspector'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('inspector-resize-handle')), findsNothing);
    expect((await store.load()).layout.inspectorOpen, isFalse);
    await tester.tap(find.byTooltip('Open workspace inspector'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('inspector-resize-handle')), findsOneWidget);
    expect(tester.getSize(find.byType(WorkspaceInspector)).width, 420);
    expect((await store.load()).layout.inspectorOpen, isTrue);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  for (final scale in [1.0, 1.3, 1.6]) {
    testWidgets(
        'narrow production inspector retains composer draft at scale $scale',
        (tester) async {
      tester.view.physicalSize = const Size(720, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(OdessyApp(
          api: WiredCaptureApi(),
          preferencesStore: ScaledPreferences(scale),
          projectStore: CaptureProjects(),
          setupBackend: SavedComponents()));
      await tester.pumpAndSettle();
      await tester.enterText(
          find.byKey(const Key('composer')), 'retained narrow draft');
      await tester.tap(find.byTooltip('Open workspace inspector'));
      await tester.pumpAndSettle();
      expect(find.byType(WorkspaceInspector), findsOneWidget);
      await tester.tap(find.byTooltip('Close workspace inspector'));
      await tester.pumpAndSettle();
      expect(find.text('retained narrow draft'), findsOneWidget);
      await tester.tap(find.byTooltip('Open navigation menu'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Jobs').hitTestable().last);
      await tester.pumpAndSettle();
      expect(find.byType(Drawer), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
    });
  }
}
