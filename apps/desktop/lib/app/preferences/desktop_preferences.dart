/// Safe local presentation values only: no credentials, paths or permissions.
enum DesktopTheme { system, light, dark }

class DesktopPreferences {
  const DesktopPreferences(
      {this.theme = DesktopTheme.dark,
      this.textScale = 1,
      this.reduceTransparency = false});
  final DesktopTheme theme;
  final double textScale;
  final bool reduceTransparency;

  factory DesktopPreferences.fromJson(Map<String, dynamic> data) {
    final theme = data['theme'];
    final scale = data['text_scale'];
    if (data.keys.any((key) => !const {
              'theme',
              'text_scale',
              'reduce_transparency'
            }.contains(key)) ||
        !DesktopTheme.values.any((v) => v.name == theme) ||
        scale is! num ||
        !scale.isFinite ||
        scale < 1 ||
        scale > 1.6 ||
        data['reduce_transparency'] is! bool) {
      throw const FormatException('Invalid desktop preferences');
    }
    return DesktopPreferences(
        theme: DesktopTheme.values.byName(theme as String),
        textScale: scale.toDouble(),
        reduceTransparency: data['reduce_transparency'] as bool);
  }

  Map<String, dynamic> toJson() => {
        'theme': theme.name,
        'text_scale': textScale,
        'reduce_transparency': reduceTransparency
      };
}

class PreferencesSnapshot {
  const PreferencesSnapshot(this.value, this.revision);
  final DesktopPreferences value;
  final int revision;
}

abstract interface class DesktopPreferencesStore {
  Future<PreferencesSnapshot> load();
  Future<PreferencesSnapshot> save(DesktopPreferences value,
      {required int revision});
}

class PreferencesConflict implements Exception {
  const PreferencesConflict();
  @override
  String toString() =>
      'Preferences changed or another writer is active. Reload before saving.';
}
