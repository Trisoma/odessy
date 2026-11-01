import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
import 'section_draft.dart';

class ComponentReviewFailure implements Exception {
  ComponentReviewFailure(this.message, this.fields);
  final String message;
  final Map<String, String> fields;
}

class DraftMergeReview {
  DraftMergeReview(this.revision, this.owner, Json edited, Json latest,
      Json merged, this.conflicts)
      : edited = Map.unmodifiable(edited),
        latest = Map.unmodifiable(latest),
        merged = Map.unmodifiable(merged);
  final String revision, owner;
  final Json edited, latest, merged;
  final List<String> conflicts;
}

/// App-owned drafts and admitted writes scoped to one backend/installation root.
class ComponentSettingsController extends ChangeNotifier {
  ComponentSettingsController(SetupBackend source, {this.root = ''})
      : backend = source is SetupMetadataBackend
            ? (source as SetupMetadataBackend).createMetadataReader()
            : source,
        ownsBackend = source is SetupMetadataBackend;
  final SetupBackend backend;
  final String root;
  final bool ownsBackend;
  Json snapshot = {};
  final Map<String, SectionDraft> drafts = {};
  final Map<String, String> draftOwners = {};
  String? activeSection;
  bool busy = false, _disposed = false, _closed = false;
  bool inspectionRequired = false;
  String error = '', status = '';
  Map<String, String> fieldErrors = {};
  Json? writerReview;
  bool writerBlocked=false;
  Future<bool>? _write;
  Json? _submitted;
  String? _submittedSection;
  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Json _values(String section) {
    final selection = snapshot['selection'] as Map? ?? {};
    return Map<String, dynamic>.from(section == 'llm'
        ? selection['model'] as Map? ?? {}
        : section == 'host'
            ? {'role': selection['role'], 'workspace': snapshot['workspace']}
            : selection['voice'] as Map? ?? {});
  }

  Json _project(String section, Json shape) {
    final value = _values(section);
    return {
      for (final key in shape.keys)
        key: value[key] ?? (key == 'enabled' ? false : '')
    };
  }

  String get _owner =>
      (snapshot['configuration'] as Map?)?['owner'] as String? ?? root;
  SectionDraft draft(String section, Json initial) =>
      drafts.putIfAbsent(section, () {
        draftOwners[section] = _owner;
        return SectionDraft(initial, snapshot['revision'] as String);
      });
  void edit(String section, Json value) {
    if (busy || _disposed || inspectionRequired) return;
    drafts[section]?.edit(value);
    error = '';
    fieldErrors = {};
    if (!inspectionRequired) {
      status = drafts[section]?.dirty == true ? 'Unsaved changes.' : '';
    }
    _notify();
  }

  void discard(String section) {
    drafts[section]?.discard();
    if (status == 'Unsaved changes.') status = '';
    _notify();
  }

  bool get dirty => drafts[activeSection]?.dirty ?? false;
  Future<Json> _call(String action,
      {String? section, Json? patch, String? confirm, String? revision}) async {
    final reply = await backend.execute(SetupRequest(
        id: 'component-${DateTime.now().microsecondsSinceEpoch}',
        operation: 'component-settings',
        selection: const {},
        parameters: {
          'action': action,
          'root': root,
          if (section != null) 'section': section,
          if (patch != null) 'patch': patch,
          if (action != 'load') 'revision': revision ?? snapshot['revision'],
          if (confirm != null) 'confirm': confirm
        }));
    if (!reply.ok) {
      if (reply.fieldErrors.isNotEmpty) {
        throw ComponentReviewFailure(reply.reason, reply.fieldErrors);
      }
      throw StateError(reply.reason.isNotEmpty ? reply.reason : reply.error);
    }
    return reply.result;
  }

  Future<void> load() async {
    if (busy || _disposed) return;
    busy = true;
    error = '';
    status = 'Loading current configuration…';
    _notify();
    try {
      snapshot = await _call('load');
      status = '';
      writerBlocked=(snapshot['writer'] as Map?)?['pending']==true;
      if (writerBlocked) {
        writerReview={'recoverable':false,'status':(snapshot['writer'] as Map)['status']};
        status=writerReview!['status'] as String;
      }
      for (final entry in drafts.entries) {
        if (!entry.value.dirty) {
          entry.value.accept(_project(entry.key, entry.value.values),
              snapshot['revision'] as String);
        }
      }
      if (inspectionRequired) {
        final actual = _values(_submittedSection!);
        final matches =
            _submitted!.entries.every((e) => actual[e.key] == e.value);
        if (matches) {
          final d = drafts[_submittedSection];
          if (d != null) {
            d.accept(_project(_submittedSection!, d.values),
                snapshot['revision'] as String);
          }
          inspectionRequired = false;
          status =
              'Inspection found the requested saved values. Restart Odessy to apply.';
        } else {
          status =
              'Inspection found different saved values. Reload this section before a new save. No write was replayed.';
        }
      }
    } catch (_) {
      error =
          'Could not inspect configuration. Check the local helper and retry inspection.';
      status =
          inspectionRequired ? 'Inspect saved values before another save.' : '';
    } finally {
      busy = false;
      _notify();
      if (_disposed) await _close();
    }
  }

  Future<void> reloadSection(String section) async {
    await load();
    if (error.isNotEmpty || busy) return;
    final d = drafts[section];
    if (d != null) {
      d.accept(_project(section, d.values), snapshot['revision'] as String);
    }
    if (_submittedSection == section) inspectionRequired = false;
    status = 'Loaded saved values. This section’s draft was discarded.';
    _notify();
  }

  Future<bool> save(String section, Json patch) {
    if (busy ||
        _disposed ||
        inspectionRequired ||
        writerBlocked ||
        snapshot['revision'] == null) {
      return Future.value(false);
    }
    final future = _save(section, Map<String, dynamic>.unmodifiable(patch));
    _write = future;
    return future;
  }

  Future<bool> _save(String section, Json patch) async {
    busy = true;
    error = '';
    status = 'Validating and saving this section…';
    _notify();
    bool dispatched = false;
    final revision =
        drafts[section]?.revision ?? snapshot['revision'] as String;
    try {
      final review = await _call('review',
          section: section, patch: patch, revision: revision);
      _submitted = patch;
      _submittedSection = section;
      dispatched = true;
      snapshot = await _call('save',
          section: section,
          patch: patch,
          revision: revision,
          confirm: review['plan_id'] as String);
      final d = drafts[section];
      if (d != null) {
        d.accept(_project(section, d.values), snapshot['revision'] as String);
      }
      for (final entry in drafts.entries) {
        if (!entry.value.dirty) {
          entry.value.revision = snapshot['revision'] as String;
        }
      }
      status =
          'Saved. Restart Odessy to apply. Other components were preserved.';
      return true;
    } catch (e) {
      inspectionRequired = dispatched;
      error = dispatched
          ? 'Save outcome is unknown; no success assumed.'
          : e is ComponentReviewFailure
              ? e.message
              : e is StateError
                  ? e.message.toString()
                  : 'Configuration review failed.';
      if (e is ComponentReviewFailure) fieldErrors = e.fields;
      status = dispatched
          ? 'Inspect saved values before another save. Draft retained.'
          : 'Not saved. Draft retained; correct the fields or reload changed settings.';
      return false;
    } finally {
      busy = false;
      _notify();
      if (_disposed) await _close();
    }
  }

  Future<void> _close() async {
    if (_closed) return;
    _closed = true;
    if (ownsBackend) await backend.close();
  }

  Future<DraftMergeReview?> reviewMerge(String section) async {
    final d = drafts[section];
    if (busy || inspectionRequired || d == null || !d.dirty) return null;
    final edited = Map<String, dynamic>.of(d.values),
        baseline = Map<String, dynamic>.of(d.baseline);
    await load();
    if (error.isNotEmpty ||
        draftOwners[section] != _owner ||
        !mapEquals(d.values, edited)) {
      error = 'Configuration owner or draft changed. No merge performed.';
      _notify();
      return null;
    }
    final latest = _project(section, edited);
    final touched = edited.keys.where((key) => edited[key] != baseline[key]);
    return DraftMergeReview(
        snapshot['revision'] as String, _owner, edited, latest, {
      ...latest,
      for (final key in touched) key: edited[key]
    }, [
      for (final key in touched)
        if (latest[key] != baseline[key] && latest[key] != edited[key]) key
    ]);
  }

  bool acceptMerge(String section, DraftMergeReview review) {
    final d = drafts[section];
    if (busy ||
        inspectionRequired ||
        d == null ||
        snapshot['revision'] != review.revision ||
        _owner != review.owner ||
        !mapEquals(d.values, review.edited)) {
      return false;
    }
    d.accept(review.latest, review.revision);
    d.edit(review.merged);
    error = '';
    fieldErrors = {};
    status =
        'Merged draft reviewed. Press Save to submit a new reviewed change.';
    _notify();
    return true;
  }

  Future<void> inspectWriter() async {
    if (busy || _disposed || root.isNotEmpty) return;
    busy = true;
    _notify();
    try {
      writerReview = await _call('lock-inspect');
      status = writerReview!['status'] as String;
    } catch (_) {
      error = 'Writer inspection failed; no recovery performed.';
    } finally {
      busy = false;
      _notify();
    }
  }

  Future<void> recoverWriter(String reviewedPlan) async {
    if (busy || _disposed || writerReview?['plan_id'] != reviewedPlan) return;
    busy = true;
    _notify();
    try {
      final result = await _call('lock-recover', confirm: reviewedPlan);
      writerReview = null;
      writerBlocked = false;
      status = result['status'] as String;
      error = '';
    } catch (_) {
      error =
          'Writer state changed or is active. Inspect again; no recovery assumed.';
    } finally {
      busy = false;
      _notify();
    }
  }

  Future<void> shutdown() async {
    await _write;
    await _close();
  }

  @override
  void dispose() {
    _disposed = true;
    if (!busy) _close();
    super.dispose();
  }
}
