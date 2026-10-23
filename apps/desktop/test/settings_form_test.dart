import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/features/settings/controllers/settings_form_controller.dart';
import 'package:odessy_desktop/features/settings/controllers/setting_field.dart';

void main() {
  SettingField<int> field() => SettingField(
      source: 'service',
      applyTiming: 'new sessions',
      parse: int.tryParse,
      validate: (v) => null);
  test('loading and disposal suppress late results', () async {
    final pending = Completer<int>();
    final f = field();
    final c = SettingsFormController(
        field: f, read: () => pending.future, write: (_) async {});
    final load = c.load();
    expect(c.loading, true);
    c.dispose();
    pending.complete(1);
    await load;
    expect(c.loaded, false);
  });
  test('failure retains dirty draft; successful save reloads', () async {
    var stored = 1;
    var fail = false;
    final f = field();
    final c = SettingsFormController(
        field: f,
        read: () async => stored,
        write: (v) async {
          if (fail) throw StateError('offline');
          stored = v;
        });
    await c.load();
    c.edit('4');
    fail = true;
    await c.save();
    expect(c.error, isNotNull);
    expect(f.dirty, true);
    expect(f.value, 4);
    fail = false;
    await c.save();
    expect(f.dirty, false);
    expect(stored, 4);
    c.dispose();
  });
  test('load failure is recoverable', () async {
    final c = SettingsFormController(
        field: field(),
        read: () async => throw StateError('offline'),
        write: (_) async {});
    await c.load();
    expect(c.error, isNotNull);
    expect(c.loading, false);
    expect(c.loaded, false);
    c.dispose();
  });
}
