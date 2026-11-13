import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/preferences/desktop_preferences.dart';
import 'package:odessy_desktop/app/preferences/local_preferences_store.dart';
import 'package:odessy_desktop/app/setup/instance_guard.dart';

void main() {
  late Directory directory;
  late LocalPreferencesStore store;
  setUp(() async {
    directory =
        await Directory.systemTemp.createTemp('odessy-preferences-test-');
    store = LocalPreferencesStore(directory);
  });
  tearDown(() async => directory.delete(recursive: true));

  test('defaults do not write; durable values reopen in a fresh owner',
      () async {
    expect((await store.load()).value.theme, DesktopTheme.dark);
    expect(await directory.list().isEmpty, isTrue);
    final saved = await store.save(
        const DesktopPreferences(
            theme: DesktopTheme.light,
            textScale: 1.4,
            reduceTransparency: true),
        revision: 0);
    final reloaded = await LocalPreferencesStore(directory).load();
    expect(reloaded.revision, saved.revision);
    expect(reloaded.value.theme, DesktopTheme.light);
    expect(reloaded.value.textScale, 1.4);
    expect(reloaded.value.reduceTransparency, isTrue);
    expect(await directory.list().where((e) => e.path.endsWith('.tmp')).isEmpty,
        isTrue);
    final document =
        await File('${directory.path}/preferences.json').readAsString();
    expect(document, isNot(contains('token')));
  });

  test('stale or concurrent writer cannot overwrite a successful save',
      () async {
    await store.save(const DesktopPreferences(theme: DesktopTheme.light),
        revision: 0);
    await expectLater(store.save(const DesktopPreferences(), revision: 0),
        throwsA(isA<PreferencesConflict>()));
    final guard = await InstanceGuard.acquire('preferences',
        directory: directory, mode: 'writer');
    try {
      await expectLater(store.save(const DesktopPreferences(), revision: 1),
          throwsA(isA<PreferencesConflict>()));
    } finally {
      await guard!.release();
    }
    expect((await store.load()).value.theme, DesktopTheme.light);
    expect((await store.save(const DesktopPreferences(), revision: 1)).revision,
        2);
  });

  test('corrupt, future, oversized and credential-bearing files are preserved',
      () async {
    final file = File('${directory.path}/preferences.json');
    for (final document in [
      '{bad json',
      jsonEncode({
        'schema_version': 2,
        'revision': 1,
        'preferences': const DesktopPreferences().toJson()
      }),
      jsonEncode({
        'schema_version': 1,
        'revision': 1,
        'preferences': {
          ...const DesktopPreferences().toJson(),
          'token': 'fixture'
        }
      }),
      'x' * (LocalPreferencesStore.maxBytes + 1),
    ]) {
      await file.writeAsString(document);
      await expectLater(store.load(), throwsFormatException);
      await expectLater(store.save(const DesktopPreferences(), revision: 0),
          throwsFormatException);
      expect(await file.readAsString(), document);
    }
  });

  test('invalid presentation values fail before writing', () async {
    await expectLater(
        store.save(const DesktopPreferences(textScale: 0.5), revision: 0),
        throwsFormatException);
    expect(await directory.list().isEmpty, isTrue);
  });
}
