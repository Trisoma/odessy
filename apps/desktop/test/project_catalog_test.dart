import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/workspaces/local_project_store.dart';
import 'package:odessy_desktop/features/workspaces/controllers/project_catalog_controller.dart';
import 'package:odessy_desktop/features/workspaces/presentation/project_catalog_page.dart';

class CatalogApi extends DemoApi
    implements SessionDirectoryApi, RuntimeConfigurationApi {
  String instance = 'a' * 32, engine = 'legacy';
  int mutations = 0;
  bool busy = false, fail = false;
  Completer<SessionDirectoryPage>? delayed;
  @override
  String get identity => 'http://127.0.0.1:9000';
  @override
  Future<RuntimeCapabilities> runtimeCapabilities() async {
    if (fail) throw StateError('offline');
    return RuntimeCapabilities.fromJson({
      'schema_version': 1,
      'service_instance_id': instance,
      'restart_safe': true,
      'configuration': {
        'owner': 'b' * 64,
        'fingerprint': 'c' * 64,
        'fingerprints': {
          for (final k in ['llm', 'stt', 'tts', 'voice', 'host']) k: 'd' * 64
        },
        'workspace': 'C:\\configured',
        'model': {'name': 'local'}
      }
    });
  }

  @override
  Future<SessionDirectoryPage> listSessions(
          {int limit = 50, int offset = 0}) async =>
      delayed?.future ??
      SessionDirectoryPage.fromJson({
        'service_instance_id': instance,
        'sessions': [
          {
            'id': 'session-1',
            'engine': engine,
            'selected_profile': 'local',
            'running': busy
          }
        ],
        'next_offset': null
      });
  @override
  Future<Session> snapshot(String sid) async =>
      Session(sid, selectedProfile: 'local', running: busy);
  @override
  Future<Session> createSession() async {
    mutations++;
    throw StateError('unexpected creation');
  }

  @override
  Future<void> deleteSession(String sid) async {
    mutations++;
  }

  @override
  Future<void> send(String sid, String text) async {
    mutations++;
  }

  @override
  Future<String> prepareRestart(String serviceInstance) async =>
      throw StateError('unexpected restart');
  @override
  Future<void> resumeAdmission(String ticket) async =>
      throw StateError('unexpected restart');
}

void main() {
  late Directory directory;
  late LocalProjectStore store;
  late CatalogApi api;
  late ProjectCatalogController controller;
  setUp(() async {
    directory = await Directory.systemTemp.createTemp('odessy-projects-test-');
    store = LocalProjectStore(directory);
    api = CatalogApi();
    controller = ProjectCatalogController(
        store: store,
        directory: api,
        runtime: api,
        api: api,
        serviceOrigin: api.identity);
  });
  tearDown(() async {
    controller.dispose();
    api.close();
    await directory.delete(recursive: true);
  });

  for (final engine in ['legacy', 'harness']) {
    test(
        '$engine grouping survives fresh owner, selection is exact, rename never replays work',
        () async {
      api.engine = engine;
      await controller.refresh();
      await controller.registerCurrent('Odessy', sessionId: 'session-1');
      final id = controller.records.single.id;
      await controller.rename(id, 'My project');
      expect((await LocalProjectStore(directory).load()).records.single.label,
          'My project');
      final selection = await controller.select(id, 'session-1');
      expect(selection.engine, engine);
      expect(selection.profile, 'local');
      expect(selection.serviceInstanceId, api.instance);
      expect(api.mutations, 0);
      api.busy = true;
      await expectLater(controller.select(id, 'session-1'), throwsStateError);
      api.busy = false;
      api.instance = 'e' * 32;
      await expectLater(controller.select(id, 'session-1'), throwsStateError);
      await controller.refresh();
      expect(controller.matches(controller.records.single), isFalse);
      expect(controller.status(controller.records.single),
          contains('Unavailable'));
      expect(api.mutations, 0);
    });
  }

  test(
      'metadata-only move/forget keeps all sessions; search uses actual summaries',
      () async {
    await controller.refresh();
    await controller.registerCurrent('One');
    await controller.groupSession(controller.records.single.id, 'session-1');
    controller.search('harness');
    expect(controller.filteredSessions, isEmpty);
    controller.search('local');
    expect(controller.filteredSessions.single.id, 'session-1');
    await controller.removeGroup(controller.records.single.id);
    expect(controller.records, isEmpty);
    expect(api.mutations, 0);
  });

  test(
      'revision, corrupt/future files, invalid origins and unknown secret keys fail safely',
      () async {
    expect((await store.load()).revision, 0);
    expect(await directory.list().isEmpty, isTrue);
    await controller.refresh();
    await controller.registerCurrent('One');
    final before = await store.load();
    await store.save(before.records, revision: before.revision);
    await expectLater(store.save([], revision: before.revision),
        throwsA(isA<ProjectCatalogConflict>()));
    final file = File('${directory.path}/projects.json');
    for (final bad in [
      '{invalid',
      jsonEncode({'schema_version': 2, 'revision': 1, 'projects': []})
    ]) {
      await file.writeAsString(bad);
      await expectLater(store.load(), throwsFormatException);
      await expectLater(store.save([], revision: 0), throwsFormatException);
      expect(await file.readAsString(), bad);
    }
    for (final origin in [
      'https://example.com',
      'http://user:pass@localhost',
      'http://localhost/?token=x'
    ]) {
      expect(() => checkedOrigin(origin), throwsFormatException);
    }
    expect(
        () => ProjectRecord.fromJson(
            {...before.records.single.toJson(), 'token': 'never-store'}),
        throwsFormatException);
  });

  test(
      'offline refresh keeps metadata and invalidation rejects delayed session results',
      () async {
    await controller.refresh();
    await controller.registerCurrent('Saved');
    api.fail = true;
    await controller.refresh();
    expect(controller.records.single.label, 'Saved');
    expect(controller.sessions, isEmpty);
    expect(controller.error, isNotNull);
    api.fail = false;
    api.delayed = Completer<SessionDirectoryPage>();
    final task = controller.refresh();
    await Future<void>.delayed(Duration.zero);
    controller.invalidate();
    api.delayed!.complete(SessionDirectoryPage.fromJson({
      'service_instance_id': api.instance,
      'sessions': [],
      'next_offset': null
    }));
    await task;
    expect(controller.current, isNull);
  });

  testWidgets(
      'narrow catalog retains configuration route and opens only verified live session',
      (tester) async {
    await tester.runAsync(() async {
      await controller.refresh();
      await controller.registerCurrent('Odessy', sessionId: 'session-1');
    });
    tester.view.resetPhysicalSize();
    tester.view.physicalSize = const Size(520, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    String? opened;
    var configured = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ProjectCatalogPage(
                controller: controller,
                onOpenSession: (sid) => opened = sid,
                onConfigureWorkspace: () => configured++))));
    await tester.tap(find.text('Configure workspace and grants'));
    await tester.pump();
    expect(configured, 1);
    await tester.ensureVisible(find.text('session-1').first);
    await tester.tap(find.text('session-1').first);
    await tester.pumpAndSettle();
    expect(opened, 'session-1');
    expect(api.mutations, 0);
    expect(tester.takeException(), isNull);
  });
}
