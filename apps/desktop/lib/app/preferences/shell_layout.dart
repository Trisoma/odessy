/// Presentation-only layout state. Never contains service authority or paths.
class ShellLayout {
  const ShellLayout(
      {this.sidebarCollapsed = false,
      this.inspectorWidth = 390,
      this.inspectorOpen = false});
  final bool sidebarCollapsed;
  final double inspectorWidth;
  final bool inspectorOpen;

  ShellLayout copyWith(
          {bool? sidebarCollapsed,
          double? inspectorWidth,
          bool? inspectorOpen}) =>
      ShellLayout(
          sidebarCollapsed: sidebarCollapsed ?? this.sidebarCollapsed,
          inspectorWidth: inspectorWidth ?? this.inspectorWidth,
          inspectorOpen: inspectorOpen ?? this.inspectorOpen);

  Map<String, dynamic> toJson() => {
        'sidebar_collapsed': sidebarCollapsed,
        'inspector_width': inspectorWidth,
        'inspector_open': inspectorOpen,
      };

  factory ShellLayout.fromJson(Map<String, dynamic> data) {
    final width = data['inspector_width'];
    if (data.length != 3 ||
        data.keys.any((key) => !const {
              'sidebar_collapsed',
              'inspector_width',
              'inspector_open'
            }.contains(key)) ||
        data['sidebar_collapsed'] is! bool ||
        data['inspector_open'] is! bool ||
        width is! num ||
        !width.isFinite ||
        width < 320 ||
        width > 480) {
      throw const FormatException('Invalid shell layout');
    }
    return ShellLayout(
        sidebarCollapsed: data['sidebar_collapsed'] as bool,
        inspectorWidth: width.toDouble(),
        inspectorOpen: data['inspector_open'] as bool);
  }
}

class ShellLayoutSnapshot {
  const ShellLayoutSnapshot(this.layout, this.revision);
  final ShellLayout layout;
  ShellLayout get value => layout;
  final int revision;
}

abstract interface class ShellLayoutStore {
  Future<ShellLayoutSnapshot> load();
  Future<ShellLayoutSnapshot> save(ShellLayout value, {required int revision});
}

class ShellLayoutConflict implements Exception {
  const ShellLayoutConflict();
  @override
  String toString() =>
      'Layout changed or another writer is active. Reload before saving.';
}

class MemoryShellLayoutStore implements ShellLayoutStore {
  MemoryShellLayoutStore([ShellLayout initial = const ShellLayout()])
      : _snapshot =
            ShellLayoutSnapshot(ShellLayout.fromJson(initial.toJson()), 0);
  ShellLayoutSnapshot _snapshot;
  @override
  Future<ShellLayoutSnapshot> load() async => _snapshot;
  @override
  Future<ShellLayoutSnapshot> save(ShellLayout value,
      {required int revision}) async {
    ShellLayout.fromJson(value.toJson());
    if (revision != _snapshot.revision) throw const ShellLayoutConflict();
    return _snapshot = ShellLayoutSnapshot(value, revision + 1);
  }
}
