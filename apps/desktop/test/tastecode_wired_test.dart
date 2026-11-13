import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
import 'package:odessy_desktop/features/workspaces/presentation/workspace_inspector.dart';
import 'package:odessy_desktop/features/workspaces/controllers/workspace_inspector_controller.dart';
import 'package:odessy_desktop/features/workspaces/controllers/project_catalog_controller.dart';
import 'package:odessy_desktop/features/settings/presentation/settings.dart';
import 'component_settings_test.dart' show SavedComponents;
import 'conversation_presentation_test.dart'
    show ConversationFixtureApi, interactionFixture;
import 'project_catalog_test.dart' show CatalogApi;

class CaptureProjects implements ProjectCatalogStore {
  ProjectCatalogSnapshot value = ProjectCatalogSnapshot([], 0);
  @override
  Future<ProjectCatalogSnapshot> load() async => value;
  @override
  Future<ProjectCatalogSnapshot> save(List<ProjectRecord> records,
          {required int revision}) async =>
      value = ProjectCatalogSnapshot(records, revision + 1);
}

class WiredCaptureApi extends ConversationFixtureApi
    implements
        SessionDirectoryApi,
        RuntimeConfigurationApi,
        WorkspaceInspectorApi,
        UsageHistoryApi {
  final catalog = CatalogApi();
  @override
  Future<Json> usageHistory(
          {int days = 30,
          String? provider,
          String? model,
          int limit = 50,
          int offset = 0}) =>
      UsagePreviewApi().usageHistory(
          days: days,
          provider: provider,
          model: model,
          limit: limit,
          offset: offset);

  @override
  String get identity => 'http://127.0.0.1:9000';
  @override
  Future<RuntimeCapabilities> runtimeCapabilities() =>
      catalog.runtimeCapabilities();
  @override
  Future<String> prepareRestart(String instance) async =>
      throw StateError('Fixture does not restart');
  @override
  Future<void> resumeAdmission(String ticket) async {}
  @override
  Future<SessionDirectoryPage> listSessions(
          {int limit = 50, int offset = 0}) async =>
      SessionDirectoryPage.fromJson({
        'service_instance_id': 'a' * 32,
        'sessions': [
          {
            'id': 'demo',
            'engine': 'legacy',
            'selected_profile': 'local',
            'running': false
          }
        ],
        'next_offset': null
      });
  Json contextFor(String sid) => {
        'service_instance_id': 'a' * 32,
        'session_id': sid,
        'workspace_id': 'fixture-workspace'
      };
  @override
  Future<WorkspaceDescriptor> workspace(String sid) async =>
      WorkspaceDescriptor.fromJson({
        ...contextFor(sid),
        'label': 'Offline fixture workspace',
        'capabilities': {
          'read_files': true,
          'proposed_changes': true,
          'recorded_output': true
        }
      });
  @override
  Future<WorkspaceTree> listWorkspace(String sid, {String path = '.'}) async =>
      WorkspaceTree.fromJson({
        ...contextFor(sid),
        'path': path,
        'entries': [
          {'name': 'readme.txt', 'path': 'readme.txt', 'kind': 'file'}
        ],
        'truncated': false
      });
  @override
  Future<WorkspaceTextFile> readWorkspaceFile(String sid, String path) async =>
      WorkspaceTextFile.fromJson({
        ...contextFor(sid),
        'path': path,
        'text': 'Hello Odessy\n',
        'sha256': 'a' * 64,
        'truncated': false
      });
}

void main() {
  testWidgets('production composition navigation inspector and capture',
      (tester) async {
    for (final (family, asset) in [
      ('OdessySans', 'assets/fonts/roboto-regular.ttf'),
      ('OdessyMono', 'assets/fonts/roboto-mono.ttf'),
      ('MaterialIcons', 'fonts/MaterialIcons-Regular.otf')
    ]) {
      await (FontLoader(family)..addFont(rootBundle.load(asset))).load();
    }
    tester.view.physicalSize = const Size(1600, 1050);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final key = GlobalKey();
    final api = WiredCaptureApi()
      ..messages = [
        const ChatMessage('user', 'Review my workspace changes.'),
        const ChatMessage('assistant',
            'Review the exact change before approving. This capture uses offline fixtures; no command or microphone is active.')
      ];
    api.pending = [
      {
        ...interactionFixture(),
        'proposed_change': {
          'session_id': 'demo',
          'path': 'readme.txt',
          'before_sha256': 'a' * 64,
          'after_sha256': 'b' * 64,
          'before_text': 'Hello Odessy\n',
          'after_text': 'Hello Odessy!\n'
        }
      }
    ];
    api.activity = [
      SessionEvent(1, 'command_output', {
        'session_id': 'demo',
        'turn_id': 'fixture-turn',
        'call_id': 'fixture-call',
        'tool_name': 'terminal.run',
        'state': 'completed',
        'stdout': 'Offline fixture output. No command was executed.',
        'stderr': '',
        'exit_code': 0,
        'truncated': false
      })
    ];
    await tester.pumpWidget(RepaintBoundary(
        key: key,
        child: OdessyApp(
            api: api,
            projectStore: CaptureProjects(),
            setupBackend: SavedComponents())));
    await tester.pumpAndSettle();
    Future<void> capture(String name) async {
      expect(tester.takeException(), isNull, reason: name);
      if (!const bool.fromEnvironment('CAPTURE_WIRED')) return;
      await tester.runAsync(() async {
        final boundary =
            key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
        final frame = await boundary.toImage(pixelRatio: 1);
        final bytes = await frame.toByteData(format: ui.ImageByteFormat.png);
        final dir = Directory('../../doc/ui/tastecode-wired');
        await dir.create(recursive: true);
        await File('${dir.path}/$name.png')
            .writeAsBytes(bytes!.buffer.asUint8List());
        frame.dispose();
      });
    }

    Future<void> nav(String name) async {
      final f = find.byKey(ValueKey('nav-$name'));
      await tester.ensureVisible(f);
      await tester.pumpAndSettle();
      expect(f.hitTestable(), findsOneWidget);
      await tester.tap(f);
      await tester.pumpAndSettle();
    }

    await capture('01-assistant-approval');
    expect(find.text('Activity & usage'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('composer')), 'My retained draft');
    await tester.ensureVisible(find.text('Voice options'));
    await tester.tap(find.text('Voice options'));
    await tester.pumpAndSettle();
    await capture('02-composer-voice');
    await tester.tap(find.text('Files · Output · Changes'));
    await tester.pumpAndSettle();
    final panel =
        tester.widget<WorkspaceInspector>(find.byType(WorkspaceInspector));
    await panel.controller.open('readme.txt', directory: false);
    await tester.pumpAndSettle();
    await capture('03-files');
    panel.controller.show(InspectorPanel.terminal);
    await tester.pumpAndSettle();
    await capture('04-output');
    panel.controller.show(InspectorPanel.changes);
    await tester.pumpAndSettle();
    await capture('05-diff');
    await tester.tap(find.byTooltip('Close workspace panel'));
    await tester.pumpAndSettle();
    expect(find.text('My retained draft'), findsOneWidget);
    api.pending = [];
    await tester
        .widget<ConversationPage>(find.byType(ConversationPage))
        .controller
        .refresh();
    await tester.pumpAndSettle();
    await nav('Activity & usage');
    await capture('06-activity-usage');
    await nav('Workspaces');
    await capture('07-workspaces');
    await nav('Devices');
    await capture('08-devices');
    await tester.tap(find.widgetWithText(ChoiceChip, 'Performance'));
    await tester.pumpAndSettle();
    await capture('09-device-performance');
    await tester.tap(find.widgetWithText(ChoiceChip, 'Overview'));
    await tester.pumpAndSettle();
    await tester
        .ensureVisible(find.byKey(const ValueKey('inspect-host_demo_jetson')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('inspect-host_demo_jetson')));
    await tester.pumpAndSettle();
    await capture('10-device-details');

    await nav('Settings');
    for (var i = 0; i < SettingsSections.all.length; i++) {
      final name = SettingsSections.all[i];
      final f = find.byKey(ValueKey('settings-section-$name'));
      await tester.ensureVisible(f);
      await tester.pumpAndSettle();
      expect(f.hitTestable(), findsOneWidget);
      await tester.tap(f);
      await tester.pumpAndSettle();
      await capture('settings-${i + 1}');
    }
    await tester.ensureVisible(find.text('Tools & installation'));
    await tester.tap(find.text('Tools & installation'));
    await tester.pumpAndSettle();
    for (final name in ['Jobs', 'Handoffs']) {
      await nav(name);
      await capture(name.toLowerCase());
    }
    tester.view.physicalSize = const Size(720, 900);
    await tester.pumpAndSettle();
    await capture('narrow-handoffs');
    await tester.tap(find.byTooltip('Open navigation menu'));
    await tester.pumpAndSettle();
    await capture('narrow-navigation');
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pumpAndSettle();
  });
}
