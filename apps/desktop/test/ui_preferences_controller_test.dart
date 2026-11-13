import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/preferences/desktop_preferences.dart';
import 'package:odessy_desktop/features/settings/controllers/desktop_preferences_controller.dart';

class FakeStore implements DesktopPreferencesStore {
  FakeStore(
      {this.snapshot = const PreferencesSnapshot(DesktopPreferences(), 0)});
  PreferencesSnapshot snapshot;
  Object? loadError;
  Object? saveError;
  Completer<PreferencesSnapshot>? saveGate;
  int loads = 0;
  int saves = 0;
  final savedRevisions = <int>[];

  @override
  Future<PreferencesSnapshot> load() async {
    loads++;
    if (loadError != null) throw loadError!;
    return snapshot;
  }

  @override
  Future<PreferencesSnapshot> save(DesktopPreferences value,
      {required int revision}) async {
    saves++;
    savedRevisions.add(revision);
    if (saveGate != null) return saveGate!.future;
    if (saveError != null) throw saveError!;
    snapshot = PreferencesSnapshot(value, revision + 1);
    return snapshot;
  }
}

void main() {
  test('load exposes the saved value and revision without writing', () async {
    final store = FakeStore(
        snapshot: const PreferencesSnapshot(
            DesktopPreferences(theme: DesktopTheme.light, textScale: 1.2), 3));
    final controller = DesktopPreferencesController(store);
    addTearDown(controller.dispose);

    await controller.load();

    expect(controller.loaded, isTrue);
    expect(controller.revision, 3);
    expect(controller.value.theme, DesktopTheme.light);
    expect(controller.dirty, isFalse);
    expect(controller.canSave, isFalse);
    expect(store.saves, 0);
  });

  test('edit is a draft and preview; save persists with the loaded revision',
      () async {
    final store = FakeStore();
    final controller = DesktopPreferencesController(store);
    addTearDown(controller.dispose);
    await controller.load();
    var notifications = 0;
    controller.addListener(() => notifications++);

    controller.edit(const DesktopPreferences(
        theme: DesktopTheme.light, textScale: 1.4, reduceTransparency: true));

    expect(controller.dirty, isTrue);
    expect(controller.value.theme, DesktopTheme.light);
    expect(controller.saved.theme, DesktopTheme.dark);
    expect(controller.canSave, isTrue);
    expect(store.saves, 0);
    expect(notifications, greaterThan(0));

    await controller.save();

    expect(store.savedRevisions, [0]);
    expect(controller.dirty, isFalse);
    expect(controller.revision, 1);
    expect(controller.value.textScale, 1.4);
    expect(controller.status, isNotEmpty);
    expect(controller.error, isEmpty);
  });

  test('conflict keeps the draft and blocks saving until reload', () async {
    final store = FakeStore();
    final controller = DesktopPreferencesController(store);
    addTearDown(controller.dispose);
    await controller.load();
    controller.edit(const DesktopPreferences(theme: DesktopTheme.light));
    store.saveError = const PreferencesConflict();

    await controller.save();

    expect(controller.dirty, isTrue);
    expect(controller.conflict, isTrue);
    expect(controller.canSave, isFalse);
    expect(controller.error, contains('changed elsewhere'));
    expect(controller.value.theme, DesktopTheme.light);

    store.saveError = null;
    await controller.load();
    expect(controller.conflict, isFalse);
    expect(controller.dirty, isFalse);
  });

  test('unreadable file shows defaults, no success and no overwrite', () async {
    final store = FakeStore()..loadError = const FormatException('bad');
    final controller = DesktopPreferencesController(store);
    addTearDown(controller.dispose);

    await controller.load();
    controller.edit(const DesktopPreferences(theme: DesktopTheme.light));
    await controller.save();

    expect(controller.loaded, isFalse);
    expect(controller.revision, isNull);
    expect(controller.canSave, isFalse);
    expect(controller.status, isEmpty);
    expect(controller.error, contains('cannot be read'));
    expect(store.saves, 0);
  });

  test('write failure retains the draft and reports no saved state', () async {
    final store = FakeStore();
    final controller = DesktopPreferencesController(store);
    addTearDown(controller.dispose);
    await controller.load();
    controller.edit(const DesktopPreferences(textScale: 1.6));
    store.saveError = StateError('disk');

    final result = await controller.save();

    expect(result, isFalse);
    expect(controller.saving, isFalse);
    expect(controller.dirty, isTrue);
    expect(controller.status, isEmpty);
    expect(controller.error, contains('Could not save'));
  });

  test('discard drops the draft without touching the stored revision',
      () async {
    final store = FakeStore();
    final controller = DesktopPreferencesController(store);
    addTearDown(controller.dispose);
    await controller.load();
    controller.edit(const DesktopPreferences(theme: DesktopTheme.system));
    await controller.discard();

    expect(controller.dirty, isFalse);
    expect(controller.value.theme, DesktopTheme.dark);
    expect(controller.revision, 0);
    expect(store.saves, 0);
  });

  test('dispose during an in-flight save does not notify or throw', () async {
    final store = FakeStore();
    final controller = DesktopPreferencesController(store);
    await controller.load();
    controller.edit(const DesktopPreferences(theme: DesktopTheme.light));
    store.saveGate = Completer<PreferencesSnapshot>();
    final pending = controller.save();
    controller.dispose();
    store.saveGate!
        .complete(const PreferencesSnapshot(DesktopPreferences(), 9));
    await expectLater(pending, completes);
  });
}
