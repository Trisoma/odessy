import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/setup/controllers/model_catalog_controller.dart';
import 'setup_controller_test.dart' show Backend;
import 'package:odessy_desktop/features/setup/presentation/model_picker.dart';
import 'package:odessy_desktop/widgets/pixel_boat.dart';
import 'package:odessy_desktop/app/setup/local_setup_backend.dart';
import 'dart:io';

class CatalogBackend extends Backend {
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {
        'status': 'suggestions',
        'models': [
          {
            'name': 'deepseek-flash',
            'label': 'Provider default (deepseek-flash)'
          }
        ]
      }
    }, request.id);
  }
}

void main() {
  testWidgets('choosing a readable default fills the exact model ID',
      (tester) async {
    final c = ModelCatalogController(CatalogBackend());
    final value = TextEditingController();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: Column(children: [
      ModelPicker(
          catalog: c, provider: 'deepseek', value: value, onChanged: () {}),
      const PixelBoat(),
    ]))));
    await tester.pumpAndSettle();
    await tester.tap(find.byType(DropdownButtonFormField<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Provider default (deepseek-flash)').last);
    await tester.pumpAndSettle();
    expect(value.text, 'deepseek-flash');
    expect(find.byType(PixelBoat), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    c.dispose();
    value.dispose();
  });
  test('real local helper returns provider choices without paid traffic',
      () async {
    final python = Platform.environment['ODESSY_TEST_PYTHON'];
    final backend = LocalSetupBackend(
        python: python!, sourceRoot: Directory.current.parent.parent.path);
    final c = ModelCatalogController(backend);
    try {
      await c.load('deepseek');
      expect(c.message, isEmpty);
      expect(c.models.single['name'], 'deepseek-flash');
    } finally {
      c.dispose();
      await backend.close();
    }
  }, skip: Platform.environment['ODESSY_TEST_PYTHON'] == null);
  test('catalog defaults are local and refresh is explicit', () async {
    final backend = CatalogBackend();
    final c = ModelCatalogController(backend);
    await c.load('deepseek');
    expect(c.models.single['name'], 'deepseek-flash');
    expect(backend.requests.single.parameters['refresh'], false);
    await c.load('deepseek', refresh: true);
    expect(backend.requests.last.parameters['refresh'], true);
    c.dispose();
  });
}
