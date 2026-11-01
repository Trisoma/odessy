import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/setup/native_picker.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/devices/presentation/pairing_page.dart';
import 'package:odessy_desktop/features/setup/controllers/maintenance_controller.dart';
import 'package:odessy_desktop/features/settings/presentation/provider_probe_panel.dart';

class AcceptanceBackend implements SetupBackend {
  final requests = <SetupRequest>[];
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    final p=request.parameters;
    final result=request.operation=='component-probe'?{'status':'configured_unverified','message':'Missing microphone permission. Text remains available.'}:
      p['review']==true?{'plan_id':'exact-review','operation':p['action'],'arguments':['peers','revoke',p['alias']]}:
      {'operation':'list','link':'fixture-link','peers':{'grant_revision':4,'peers':[
        {'alias':'laptop','installation_id':'id-one','fingerprint_sha256':'a'*64,'revoked':true,'capability_mcp':false,'capability_telemetry':false},
        {'alias':'edge','installation_id':'id-two','fingerprint_sha256':'b'*64,'revoked':false,'capability_mcp':false,'capability_telemetry':true},
      ]}};
    return SetupReply.fromJson({'id':request.id,'status':'ok','result':result},request.id);
  }

  @override
  Future<void> cancel() async {}
  @override
  Future<void> close() async {}
}

void main() {
  void viewport(WidgetTester t, Size size) {
    t.view.physicalSize = size;
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
  }

  testWidgets(
      'provider test requires consent and no coordinator; controls have accessible targets',
      (t) async {
    viewport(t, const Size(1000, 850));
    final semantics = t.ensureSemantics();
    final backend = AcceptanceBackend();
    await t.pumpWidget(MaterialApp(
        theme: odessyTheme(Brightness.dark),
        home: Scaffold(
            body: SingleChildScrollView(
                child: ProviderProbePanel(
                    backend: backend,
                    selection: const {'role': 'combined'},
                    section: 'tts',
                    picker: const UnavailablePicker())))));
    await t.pumpAndSettle();
    expect(backend.requests, isEmpty);
    await t.tap(find.text('Test speech output'));
    await t.pumpAndSettle();
    expect(find.text('Run this component test?'), findsOneWidget);
    expect(backend.requests, isEmpty);
    await t.tap(find.text('Cancel'));
    await t.pumpAndSettle();
    expect(backend.requests, isEmpty);
    await t.tap(find.text('Check configuration'));
    await t.pumpAndSettle();
    expect(backend.requests.single.parameters['run'], false);
    await expectLater(t, meetsGuideline(labeledTapTargetGuideline));
    await expectLater(t, meetsGuideline(androidTapTargetGuideline));
    semantics.dispose();
    expect(t.takeException(), isNull);
  });
  testWidgets(
      'paired trust is distinct from connectivity and editing invalidates revocation review',
      (t) async {
    viewport(t, const Size(1000, 950));
    final c = MaintenanceController(AcceptanceBackend());
    await t.pumpWidget(
        MaterialApp(home: Scaffold(body: PairingPage(controller: c))));
    await t.pumpAndSettle();
    expect(find.textContaining('Trust: Revoked'), findsOneWidget);
    expect(find.textContaining('Connection: unknown'), findsNWidgets(2));
    await t.tap(find.text('Review revocation').first);
    await t.pumpAndSettle();
    await t.ensureVisible(find.text('Review operation'));
    await t.tap(find.text('Review operation'));
    await t.pumpAndSettle();
    expect(c.canApply, true);
    expect(c.fields['alias'], 'laptop');
    c.update({...c.fields, 'alias': 'edge'});
    await t.pumpAndSettle();
    expect(c.canApply, false);
    await t.pumpWidget(const SizedBox());
    c.dispose();
  });
  testWidgets(
      'pairing remains scrollable at 200 percent text and renders review evidence',
      (t) async {
    viewport(t, const Size(840, 640));
    final c = MaintenanceController(AcceptanceBackend());
    await t.pumpWidget(MaterialApp(
        theme: odessyTheme(Brightness.dark),
        home: MediaQuery(
            data: const MediaQueryData(textScaler: TextScaler.linear(2)),
            child: Scaffold(body: PairingPage(controller: c)))));
    await t.pumpAndSettle();
    await t.scrollUntilVisible(find.text('Review operation'), 500,
        scrollable: find.byType(Scrollable).first);
    expect(t.takeException(), isNull);
    await t.pumpWidget(const SizedBox());
    c.dispose();
  });
  testWidgets('capture component and pairing screenshots', (t) async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    viewport(t, const Size(1100, 900));
    Future<void> capture(Widget child, String name) async {
      final key = GlobalKey();
      await t.pumpWidget(RepaintBoundary(
          key: key,
          child: MaterialApp(
              theme: odessyTheme(Brightness.dark),
              home: Scaffold(body: child))));
      await t.pumpAndSettle();
      await t.runAsync(() async {
        final image = await (key.currentContext!.findRenderObject()!
                as RenderRepaintBoundary)
            .toImage();
        final data = await image.toByteData(format: ui.ImageByteFormat.png);
        await Directory('build/preview/ux-completion').create(recursive: true);
        await File('build/preview/ux-completion/$name.png')
            .writeAsBytes(data!.buffer.asUint8List());
        image.dispose();
      });
    }

    await capture(
        SingleChildScrollView(
            child: ProviderProbePanel(
                backend: AcceptanceBackend(),
                selection: const {'role': 'combined'},
                section: 'tts',
                picker: const UnavailablePicker())),
        'provider-tests');
    final c = MaintenanceController(AcceptanceBackend());
    await capture(PairingPage(controller: c), 'device-pairing');
    await t.pumpWidget(const SizedBox());
    c.dispose();
  });
}
