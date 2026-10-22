import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/features/settings/controllers/setting_field.dart';

void main() {
  test('typed field validates and tracks baseline', () {
    final f = SettingField<int>(
        source: 'user',
        applyTiming: 'new sessions',
        parse: int.tryParse,
        validate: (v) => v < 0 || v > 32 ? '0–32' : null);
    f.load(1);
    expect(f.dirty, false);
    f.edit('bad');
    expect(f.error, isNotNull);
    f.edit('4');
    expect(f.value, 4);
    expect(f.dirty, true);
    f.load(4);
    expect(f.dirty, false);
  });
}
