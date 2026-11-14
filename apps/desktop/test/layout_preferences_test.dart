import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/preferences/shell_layout_store.dart';

void main() {
  test('layout survives reopening and stale revisions cannot overwrite it',
      () async {
    final root = await Directory.systemTemp.createTemp('odessy-layout-');
    addTearDown(() => root.delete(recursive: true));
    final store = LocalShellLayoutStore(root);
    expect((await store.load()).revision, 0);
    final saved = await store.save(
        const ShellLayout(
            sidebarCollapsed: true, inspectorWidth: 480, inspectorOpen: true),
        revision: 0);
    expect(saved.revision, 1);
    final reopened = LocalShellLayoutStore(root);
    final loaded = await reopened.load();
    expect(loaded.layout.toJson(), saved.layout.toJson());
    await expectLater(reopened.save(const ShellLayout(), revision: 0),
        throwsA(isA<ShellLayoutConflict>()));
    expect((await reopened.load()).revision, 1);
    final next = await reopened
        .save(loaded.layout.copyWith(inspectorWidth: 320), revision: 1);
    expect(next.revision, 2);
    expect(next.layout.inspectorWidth, 320);
  });

  test('malformed data is reported and preserved', () async {
    final root =
        await Directory.systemTemp.createTemp('odessy-layout-invalid-');
    addTearDown(() => root.delete(recursive: true));
    final file = File('${root.path}${Platform.pathSeparator}shell-layout.json');
    await file.writeAsString('{broken');
    final store = LocalShellLayoutStore(root);
    await expectLater(store.load(), throwsFormatException);
    await expectLater(
        store.save(const ShellLayout(), revision: 0), throwsFormatException);
    expect(await file.readAsString(), '{broken');
  });

  test('strict data bounds and memory revision behavior', () async {
    for (final width in [319, 481, double.nan, double.infinity]) {
      expect(
          () => ShellLayout.fromJson(
              {...const ShellLayout().toJson(), 'inspector_width': width}),
          throwsFormatException);
    }
    expect(
        () => ShellLayout.fromJson(
            {...const ShellLayout().toJson(), 'extra': true}),
        throwsFormatException);
    final store = MemoryShellLayoutStore();
    await store.save(const ShellLayout(inspectorOpen: true), revision: 0);
    expect((await store.load()).layout.inspectorOpen, true);
    await expectLater(store.save(const ShellLayout(), revision: 0),
        throwsA(isA<ShellLayoutConflict>()));
  });
}
