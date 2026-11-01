import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// Bounded discovery of existing speech assets for one settings section.
///
/// Detection owns its own busy/error state so a slow or failed scan never locks
/// unrelated settings, never disconnects the coordinator and never touches a draft.
/// Results are suggestions only: selecting one is a separate, explicit edit.
///
/// Two things this class is deliberately careful about:
///
///  * Nothing here decides what is "blocking". It reports per-field verdicts and the
///    caller asks about the fields belonging to the section it is showing, so an
///    unusable Whisper path can never stop a TTS or a Voice save.
///  * A reply is applied only if it still belongs to the newest request *and* was
///    computed for the values most recently asked about, so two checks completing out
///    of order cannot restore an error the user has already fixed.
class VoiceDiscoveryController extends ChangeNotifier {
  VoiceDiscoveryController(SetupBackend source, {this.section = ''})
      : ownsBackend = source is SetupMetadataBackend,
        backend = source is SetupMetadataBackend
            ? (source as SetupMetadataBackend).createMetadataReader()
            : source;
  final SetupBackend backend;
  final bool ownsBackend;
  final String section;
  bool busy = false, _disposed = false;
  int _epoch = 0;

  /// Sequence over every request that can write [issues] (a scan or a check).
  int _sequence = 0;

  /// The exact values the newest of those requests was computed for.
  String _requested = '';

  Json candidates = {};
  List<String> missing = const [];
  bool partial = false;
  String reason = '', error = '';

  /// Field problems reported by the helper that owns the filesystem. The
  /// presentation layer never stats a path itself.
  Map<String, String> issues = const {};
  Set<String> blocking = const {};

  String? blockingIssue(String key) =>
      blocking.contains(key) ? issues[key] : null;
  String? warningIssue(String key) =>
      issues.containsKey(key) && !blocking.contains(key) ? issues[key] : null;

  /// True when any of [keys] has a blocking verdict.
  ///
  /// Callers pass only the fields of the section they are showing: blocking is a
  /// property of a field, never of the whole configuration.
  bool blockingIn(Iterable<String> keys) => keys.any(blocking.contains);

  void _acceptIssues(Object? value) {
    final data = value is Map ? value : const {};
    issues = Map.unmodifiable({
      for (final entry in data.entries)
        if (entry.key is String &&
            entry.value is Map &&
            (entry.value as Map)['message'] is String)
          entry.key as String: '${(entry.value as Map)['message']}'
    });
    blocking = Set.unmodifiable({
      for (final entry in data.entries)
        if (entry.key is String &&
            entry.value is Map &&
            (entry.value as Map)['blocking'] == true)
          entry.key as String
    });
  }

  /// Candidate paths reported for one settings key, in scan order.
  List<String> of(String key) => [
        for (final value in (candidates[key] as List? ?? const []))
          if (value is String && value.isNotEmpty) value
      ];

  bool get found =>
      candidates.values.any((value) => value is List && value.isNotEmpty);

  /// An edit invalidates old verdicts immediately, without starting another
  /// process for every keystroke. Keep verdicts for fields that did not change.
  /// Submit/Browse and the existing reviewed save still validate actual paths.
  void selectionChanged(Json selection) {
    if (_disposed) return;
    final requested = jsonEncode(selection);
    if (requested == _requested) return;
    final previous = _requested.isEmpty
        ? const <String, dynamic>{}
        : (jsonDecode(_requested) as Map)['voice'] as Map? ?? const {};
    final next = selection['voice'] as Map? ?? const {};
    final changed = <String>{
      for (final key in {...previous.keys, ...next.keys})
        if (key is String && previous[key] != next[key]) key,
    };
    _sequence++;
    _requested = requested;
    issues = Map.unmodifiable(Map<String, String>.from(issues)
      ..removeWhere((key, _) => changed.contains(key)));
    blocking = Set.unmodifiable(blocking.difference(changed));
    notifyListeners();
  }

  /// Drop results and reject any reply still in flight. The draft is untouched.
  void invalidate() {
    _epoch++;
    _sequence++;
    _requested = '';
    candidates = {};
    missing = const [];
    partial = false;
    reason = '';
    error = '';
    issues = const {};
    blocking = const {};
    if (!_disposed) notifyListeners();
  }

  Future<void> detect(Json selection) async {
    if (_disposed) return;
    final epoch = ++_epoch;
    final sequence = ++_sequence;
    final requested = jsonEncode(selection);
    _requested = requested;
    busy = true;
    error = '';
    notifyListeners();
    try {
      final reply = await backend.execute(SetupRequest(
          id: 'voice-discover-${DateTime.now().microsecondsSinceEpoch}',
          operation: 'voice-discover',
          selection: selection));
      if (_disposed || epoch != _epoch) return;
      if (reply.ok) {
        final data = reply.result['candidates'];
        candidates = data is Map ? Map<String, dynamic>.from(data) : {};
        missing = [
          for (final value in (reply.result['missing'] as List? ?? const []))
            if (value is String && value.isNotEmpty) value
        ];
        partial = reply.result['partial'] == true;
        reason = '${reply.result['reason'] ?? ''}';
        // Verdicts are only trustworthy for the values still being asked about.
        if (sequence == _sequence && requested == _requested) {
          _acceptIssues(reply.result['issues']);
        }
      } else {
        error = reply.reason.isEmpty ? reply.error : reply.reason;
      }
    } catch (_) {
      if (!_disposed && epoch == _epoch) {
        error =
            'Detection is unavailable right now. Nothing was changed; Browse still works.';
      }
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        notifyListeners();
      }
    }
  }

  /// Re-check the path fields in the helper, without walking any directory.
  ///
  /// Does not take a new epoch: a check must not cancel an in-flight scan. It does
  /// take its own place in the sequence, and its verdicts are applied only while they
  /// are the newest request for the newest values.
  Future<void> validate(Json selection) async {
    if (_disposed) return;
    final sequence = ++_sequence;
    final requested = jsonEncode(selection);
    _requested = requested;
    try {
      final reply = await backend.execute(SetupRequest(
          id: 'voice-check-${DateTime.now().microsecondsSinceEpoch}',
          operation: 'voice-discover',
          selection: selection,
          parameters: const {'check': true}));
      if (_disposed || sequence != _sequence || requested != _requested) return;
      if (reply.ok) {
        _acceptIssues(reply.result['issues']);
        notifyListeners();
      }
    } catch (_) {
      // A failed check keeps the previous verdicts; it never blocks the section.
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _epoch++;
    _sequence++;
    if (ownsBackend) backend.close();
    super.dispose();
  }
}
