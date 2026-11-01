import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'component_settings_test.dart' show SavedComponents;

class DelayedApi extends DemoApi {
  final ready = Completer<List<String>>();
  @override
  Future<List<String>> profiles() => ready.future;
}

class FailedApi extends DemoApi {
  @override
  Future<List<String>> profiles() async => throw const ApiFailure(503);
}

class LongWorkspaceBackend extends SavedComponents {
  static const path =
      'C:/Users/Alexandra/Documents/Projects/Odessy/external-workspaces/a-very-long-project-directory-name/another-directory';
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    final reply = await super.execute(request);
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {...reply.result, 'workspace': path}
    }, request.id);
  }
}

void main() {
  void viewport(WidgetTester t, Size size) {
    t.view.physicalSize = size;
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
  }

  Future<void> nav(WidgetTester tester, String label,
      {bool settle = true}) async {
    if (find.widgetWithText(ListTile, label).evaluate().isEmpty) {
      await tester.tap(find.byTooltip('Open navigation menu'));
      await tester.pumpAndSettle();
    }
    await tester.tap(find.widgetWithText(ListTile, label));
    if (settle) {
      await tester.pumpAndSettle();
    } else {
      await tester.pump();
    }
  }

  testWidgets(
      'keyboard navigation and maintenance entry preserve existing routes',
      (tester) async {
    viewport(tester, const Size(1280, 900));
    await tester
        .pumpWidget(OdessyApp(api: DemoApi(), setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    Focus.of(tester.element(find.text('Workspaces'))).requestFocus();
    await tester.pump();
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pumpAndSettle();
    expect(find.text('Workspace configuration'), findsOneWidget);
    await nav(tester, 'Settings');
    await tester
        .tap(find.byKey(const ValueKey('settings-section-Installation')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Runtime maintenance'));
    await tester.tap(find.text('Runtime maintenance'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('native-root')), findsOneWidget);
    await tester.tap(find.byTooltip('Back to Settings'));
    await tester.pumpAndSettle();
    expect(find.text('Preferences'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets(
      'long workspace paths stay editable and do not mutate on inspection',
      (tester) async {
    viewport(tester, const Size(1280, 800));
    final backend = LongWorkspaceBackend();
    await tester.pumpWidget(OdessyApp(api: DemoApi(), setupBackend: backend));
    await tester.pumpAndSettle();
    await nav(tester, 'Workspaces');
    final field = tester
        .widget<TextField>(find.byKey(const ValueKey('component-workspace')));
    expect(field.controller!.text, LongWorkspaceBackend.path);
    expect(field.maxLines, 1);
    expect(backend.requests.where((r) => r.parameters['action'] == 'save'),
        isEmpty);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets(
      'connection dialog cancellation returns focus without replacing service',
      (tester) async {
    viewport(tester, const Size(1280, 800));
    await tester
        .pumpWidget(OdessyApp(api: DemoApi(), setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Connect local service'));
    await tester.pumpAndSettle();
    expect(find.text('Connect to Odessy'), findsOneWidget);
    await tester.tap(find.widgetWithText(TextButton, 'Cancel'));
    await tester.pumpAndSettle();
    expect(find.text('Connect to Odessy'), findsNothing);
    expect(find.text('DEMO'), findsWidgets);
    await nav(tester, 'Settings');
    expect(find.text('Preferences'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('navigation stays usable while session loading or failed',
      (tester) async {
    viewport(tester, const Size(1280, 900));
    final api = DelayedApi();
    await tester
        .pumpWidget(OdessyApp(api: api, setupBackend: SavedComponents()));
    await tester.pump();
    expect(find.byType(CircularProgressIndicator), findsWidgets);
    await nav(tester, 'Devices', settle: false);
    expect(find.text('Overview'), findsWidgets);
    api.ready.complete(['auto', 'local']);
    await tester.pumpAndSettle();
    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(
        OdessyApp(api: FailedApi(), setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    expect(find.text('Session needs attention'), findsOneWidget);
    expect(find.text('Refresh session'), findsOneWidget);
    await nav(tester, 'Settings');
    expect(find.text('Preferences'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
  for (final scale in [1.0, 1.5, 2.0]) {
    testWidgets('narrow drawer and settings support text scale $scale',
        (tester) async {
      viewport(tester, const Size(840, 640));
      await tester.pumpWidget(MediaQuery(
          data: MediaQueryData(textScaler: TextScaler.linear(scale)),
          child: OdessyApp(api: DemoApi(), setupBackend: SavedComponents())));
      await tester.pumpAndSettle();
      await nav(tester, 'Settings');
      expect(
          tester.state<ScaffoldState>(find.byType(Scaffold).first).isDrawerOpen,
          isFalse);
      expect(find.text('Preferences'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    });
  }
  testWidgets(
      'capture foundational shell with readable fonts at supported sizes',
      (tester) async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    viewport(tester, const Size(1280, 800));
    final key = GlobalKey();
    await tester.pumpWidget(RepaintBoundary(
        key: key,
        child: OdessyApp(api: DemoApi(), setupBackend: SavedComponents())));
    await tester.pumpAndSettle();
    Future<void> capture(String name) async {
      await tester.runAsync(() async {
        final image = await (key.currentContext!.findRenderObject()!
                as RenderRepaintBoundary)
            .toImage();
        final data = await image.toByteData(format: ui.ImageByteFormat.png);
        await Directory('build/preview/ux-foundation').create(recursive: true);
        await File('build/preview/ux-foundation/$name.png')
            .writeAsBytes(data!.buffer.asUint8List());
        image.dispose();
      });
    }

    await capture('assistant-dark-1280');
    await nav(tester, 'Settings');
    await capture('settings-dark-1280');
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await capture('settings-light-1280');
    await nav(tester, 'Workspaces');
    await capture('workspace-light-1280');
    await nav(tester, 'Devices');
    await tester.tap(find.text('Performance'));
    await tester.pumpAndSettle();
    await capture('devices-performance-light-1280');
    tester.view.physicalSize = const Size(840, 640);
    await tester.pumpAndSettle();
    await nav(tester, 'Settings');
    await capture('settings-light-840');
    tester.view.physicalSize = const Size(1440, 900);
    await tester.pumpAndSettle();
    await capture('settings-light-1440');
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
}
