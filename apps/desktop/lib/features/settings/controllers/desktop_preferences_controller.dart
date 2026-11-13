import 'package:flutter/foundation.dart';

import '../../../app/preferences/desktop_preferences.dart';

/// Settings-owned controller for the durable appearance preferences produced by
/// prompt 01.
///
/// The store is the authority: this controller only validates, drafts, saves and
/// reports. Secrets, endpoints and runtime choices never belong here, and no
/// write happens without an explicit [save].
class DesktopPreferencesController extends ChangeNotifier {
  DesktopPreferencesController(this.store);

  final DesktopPreferencesStore store;

  PreferencesSnapshot? _snapshot;
  DesktopPreferences? _draft;
  bool _loading = false;
  bool _saving = false;
  bool _loaded = false;
  bool _disposed = false;
  bool _conflict = false;
  String error = '';
  String status = '';

  /// Last value confirmed by the store; defaults when nothing was saved.
  DesktopPreferences get saved =>
      _snapshot?.value ?? const DesktopPreferences();

  /// Value to render right now: the draft while editing, else the saved value.
  DesktopPreferences get value => _draft ?? saved;

  int? get revision => _snapshot?.revision;
  bool get loading => _loading;
  bool get saving => _saving;
  bool get loaded => _loaded;
  bool get conflict => _conflict;
  bool get dirty => _draft != null && !_same(_draft!, saved);

  /// A stale write or an unreadable document blocks saving until a reload
  /// confirms the current stored state; the draft is never discarded here.
  bool get canSave =>
      _loaded &&
      !_loading &&
      !_saving &&
      !_conflict &&
      revision != null &&
      dirty;

  Future<void> load() async {
    if (_disposed || _loading) return;
    _loading = true;
    error = '';
    status = '';
    notifyListeners();
    try {
      final next = await store.load();
      if (_disposed) return;
      _snapshot = next;
      _draft = null;
      _loaded = true;
      _conflict = false;
    } on FormatException {
      if (_disposed) return;
      _loaded = false;
      _conflict = false;
      error =
          'The stored appearance preferences cannot be read. They were left untouched; defaults are shown. An older or corrupt file is preserved.';
    } catch (_) {
      if (_disposed) return;
      _loaded = false;
      _conflict = false;
      error =
          'Could not load appearance preferences. They were not changed. Retry after checking this device.';
    } finally {
      if (!_disposed) {
        _loading = false;
        notifyListeners();
      }
    }
  }

  /// Records a draft edit. Notifies so the app can apply a live preview; the
  /// value is not durable until [save] succeeds.
  void edit(DesktopPreferences next) {
    if (_disposed || _loading || _saving) return;
    _draft = next;
    // A load/save error stays visible until a later load or save resolves it;
    // typing must not hide "unreadable file" or "changed elsewhere".
    status = '';
    notifyListeners();
  }

  Future<void> discard() async {
    if (_disposed || _draft == null) return;
    _draft = null;
    error = '';
    status = '';
    notifyListeners();
  }

  Future<bool> save() async {
    if (!canSave || _disposed) return false;
    final draft = _draft!;
    _saving = true;
    error = '';
    status = '';
    notifyListeners();
    try {
      final next = await store.save(draft, revision: revision!);
      if (_disposed) return false;
      _snapshot = next;
      _draft = null;
      _loaded = true;
      _conflict = false;
      status = 'Saved to this device.';
      return true;
    } on PreferencesConflict {
      if (!_disposed) {
        _conflict = true;
        error =
            'Preferences changed elsewhere. The draft is kept; reload to compare before saving again.';
      }
      return false;
    } on FormatException {
      if (!_disposed) {
        _conflict = true;
        error =
            'The stored preferences are unreadable, so saving would overwrite them. The draft is kept; reload to inspect.';
      }
      return false;
    } catch (_) {
      if (!_disposed) {
        error =
            'Could not save appearance preferences. The draft is kept; nothing was reported as saved.';
      }
      return false;
    } finally {
      if (!_disposed) {
        _saving = false;
        notifyListeners();
      }
    }
  }

  static bool _same(DesktopPreferences a, DesktopPreferences b) =>
      a.theme == b.theme &&
      a.textScale == b.textScale &&
      a.reduceTransparency == b.reduceTransparency;

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
