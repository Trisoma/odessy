import 'package:flutter/services.dart';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/rendering.dart';
import 'package:flutter/material.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
import 'tastecode_wired_test.dart' show WiredCaptureApi, CaptureProjects;
import 'component_settings_test.dart' show SavedComponents;
import 'agent_basics_client_ux_test.dart' show WireFixture, cid;

class SavedChatApi extends WiredCaptureApi implements AgentBasicsConnection {
  final wire = WireFixture();
  @override
  late final AgentBasicsClient agentBasics = AgentBasicsClient(wire.request);
  @override
  Future<Session> snapshot(String sid) async => Session(sid,
      conversationId: sid == 'resumed-session' ? cid : null,
      messages: sid == 'resumed-session'
          ? [
              ChatMessage('user', 'hello'),
              ChatMessage('assistant', 'settled answer')
            ]
          : [],
      selectedProfile: 'local');
}

final captureKey = GlobalKey();
bool fontsLoaded = false;
Future<void> loadCaptureFonts() async {
  if (Platform.environment['ODESSY_FIX_CAPTURE_DIR'] == null || fontsLoaded) {
    return;
  }
  for (final (family, asset) in [
    ('OdessySans', 'assets/fonts/roboto-regular.ttf'),
    ('OdessyMono', 'assets/fonts/roboto-mono.ttf'),
    ('monospace', 'assets/fonts/roboto-mono.ttf'),
    ('MaterialIcons', 'fonts/MaterialIcons-Regular.otf')
  ]) {
    await (FontLoader(family)..addFont(rootBundle.load(asset))).load();
  }
  await (FontLoader('OdessySans')
        ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
        ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
      .load();
  fontsLoaded = true;
}

Future<void> capture(WidgetTester tester, String name) async {
  final directory = Platform.environment['ODESSY_FIX_CAPTURE_DIR'];
  if (directory == null) return;
  final boundary =
      captureKey.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  await tester.runAsync(() async {
    final image = await boundary.toImage(pixelRatio: 1);
    final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
    await Directory(directory).create(recursive: true);
    await File('$directory/$name.png')
        .writeAsBytes(bytes!.buffer.asUint8List());
    image.dispose();
  });
}

Future<SavedChatApi> mount(WidgetTester tester, {bool readOnly = false}) async {
  tester.view.physicalSize = const Size(1440, 1000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await loadCaptureFonts();
  final api = SavedChatApi();
  if (readOnly) api.wire.engine = 'harness';
  await tester.pumpWidget(RepaintBoundary(
      key: captureKey,
      child: OdessyApp(
          api: api,
          projectStore: CaptureProjects(),
          setupBackend: SavedComponents())));
  await tester.pumpAndSettle();
  return api;
}

Future<void> showSaved(WidgetTester t) async {
  await t.tap(find.text('Odessy project'));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('saved chat click enters conversation directly without replay',
      (t) async {
    final api = await mount(t);
    await t.enterText(find.byKey(const Key('composer')), 'unsent draft');
    await showSaved(t);
    await t.tap(find.text('Saved boat').first);
    await t.pumpAndSettle();
    await t
        .runAsync(() => Future<void>.delayed(const Duration(milliseconds: 30)));
    await t.pumpAndSettle();
    expect(find.byType(ConversationPage), findsOneWidget);
    expect(find.text('settled answer'), findsOneWidget);
    expect(api.wire.requests.where((r) => r.path.endsWith('/resume')),
        hasLength(1));
    expect(api.sends, 0);
    await capture(t, 'saved-chat');
    expect(find.text('Resume conversation'), findsNothing);
    await t.pumpWidget(const SizedBox.shrink());
  });
  testWidgets('read only chat opens transcript without runtime resume',
      (t) async {
    final api = await mount(t, readOnly: true);
    await showSaved(t);
    await t.tap(find.text('Saved boat').first);
    await t.pumpAndSettle();
    expect(find.text('settled answer'), findsOneWidget);
    expect(find.textContaining('Read-only'), findsWidgets);
    expect(api.wire.requests.where((r) => r.path.endsWith('/resume')), isEmpty);
    expect(api.sends, 0);
    await t.pumpWidget(const SizedBox.shrink());
  });
  testWidgets('secondary click offers management without resuming', (t) async {
    final api = await mount(t);
    await showSaved(t);
    await t.tap(find.text('Saved boat').first, buttons: kSecondaryMouseButton);
    await t.pumpAndSettle();
    expect(find.text('Rename'), findsOneWidget);
    expect(find.text('Delete'), findsOneWidget);
    await capture(t, 'chat-context-menu');
    expect(api.wire.requests.where((r) => r.path.endsWith('/resume')), isEmpty);
    await t.pumpWidget(const SizedBox.shrink());
  });
}
