import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

class InspectedPeer {
  const InspectedPeer(this.alias, this.installationId, this.fingerprint,
      this.telemetry, this.execution, this.address, this.workspace);
  final String alias, installationId, fingerprint;
  final bool telemetry, execution;
  final String? address, workspace;
}

/// Explicit native changes, bound to the reviewed local field snapshot.
class MaintenanceController extends ChangeNotifier {
  MaintenanceController(this.backend);
  final SetupBackend backend;
  Json fields = {}, result = {};
  String error = '', status = 'Inspect local state before making changes';
  String? reviewedAction, _confirmation;
  bool busy = false,
      needsInspection = false,
      _disposed = false,
      _mutating = false;
  int _epoch = 0;
  List<InspectedPeer>? peers;
  int? peerRevision;
  String? _peerLink;
  InspectedPeer? selectedPeer;
  bool get canApply =>
      !busy &&
      !needsInspection &&
      reviewedAction != null &&
      _confirmation != null &&
      (result['requires_inference'] != true ||
          fields['allow_inference'] == true) &&
      (result['requires_owned_model_download'] != true ||
          fields['allow_model_downloads'] == true);
  void update(Json value) {
    if (busy || needsInspection) return;
    if (value['link'] != _peerLink) {
      peers = null;
      peerRevision = null;
      _peerLink = null;
      selectedPeer = null;
    } else if (selectedPeer != null && value['alias'] != selectedPeer!.alias) {
      selectedPeer = null;
    }
    fields = Map.unmodifiable(value);
    reviewedAction = null;
    _confirmation = null;
    result = {};
    error = '';
    notifyListeners();
  }

  void selectPeer(InspectedPeer peer) {
    if (_disposed ||
        busy ||
        needsInspection ||
        peers == null ||
        !peers!.contains(peer) ||
        peerRevision == null ||
        fields['link'] != _peerLink) {
      return;
    }
    fields = Map.unmodifiable({...fields, 'alias': peer.alias});
    selectedPeer = peer;
    reviewedAction = null;
    _confirmation = null;
    result = {};
    error = '';
    notifyListeners();
  }

  void _setPeers(Json reply, Object? source) {
    final data = reply['peers'];
    if (reply['operation'] != 'list' ||
        data is! Map ||
        data['peers'] is! List ||
        data['grant_revision'] is! int ||
        (data['grant_revision'] as int) < 0 ||
        (data['peers'] as List).length > 512 ||
        source is! String ||
        source.isEmpty) {
      throw const FormatException('Invalid peer inventory');
    }
    final entries = <InspectedPeer>[], aliases = <String>{};
    for (final item in data['peers'] as List) {
      if (item is! Map ||
          item['alias'] is! String ||
          !RegExp(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
              .hasMatch(item['alias'] as String) ||
          !aliases.add(item['alias'] as String) ||
          item['installation_id'] is! String ||
          (item['installation_id'] as String).isEmpty ||
          (item['installation_id'] as String).length > 128 ||
          item['fingerprint_sha256'] is! String ||
          !RegExp(r'^[a-f0-9]{64}$')
              .hasMatch(item['fingerprint_sha256'] as String) ||
          item['capability_telemetry'] is! bool ||
          item['capability_mcp'] is! bool ||
          (item['address'] != null &&
              (item['address'] is! String ||
                  (item['address'] as String).length > 512)) ||
          (item['workspace'] != null &&
              (item['workspace'] is! String ||
                  (item['workspace'] as String).length > 4096))) {
        throw const FormatException('Invalid peer inventory');
      }
      entries.add(InspectedPeer(
          item['alias'] as String,
          item['installation_id'] as String,
          item['fingerprint_sha256'] as String,
          item['capability_telemetry'] as bool,
          item['capability_mcp'] as bool,
          item['address'] as String?,
          item['workspace'] as String?));
    }
    peers = List.unmodifiable(entries);
    peerRevision = data['grant_revision'] as int;
    _peerLink = source;
    selectedPeer = null;
  }

  Json _parameters(String action) {
    final pair = action.startsWith('pair:');
    final engine = action.startsWith('engine:');
    final actual =
        (pair || engine) ? action.substring(action.indexOf(':') + 1) : action;
    final allowed = engine
        ? {'root', 'cache', 'bundle', 'config', 'role'}
        : pair
            ? {
                'link',
                'alias',
                'certificate',
                'fingerprint',
                'address',
                'workspace',
                'output'
              }
            : {
                'root',
                'bundle',
                'config',
                'role',
                'version',
                'workspace',
                'port',
                'node_link',
                'node_server',
                'listen',
                'executable',
                'allow_inference',
                'allow_model_downloads'
              };
    final values = <String, dynamic>{
      'action': actual,
      for (final entry in fields.entries)
        if (allowed.contains(entry.key) &&
            entry.value != '' &&
            entry.value != false)
          entry.key: entry.value
    };
    if (const {
      'pair:grant-telemetry',
      'pair:revoke-telemetry',
      'pair:revoke-execution'
    }.contains(action)) {
      values.remove('workspace');
    }
    if (pair &&
        selectedPeer != null &&
        peerRevision != null &&
        fields['alias'] == selectedPeer!.alias &&
        fields['link'] == _peerLink &&
        const {
          'pair:enroll',
          'pair:grant',
          'pair:grant-telemetry',
          'pair:revoke',
          'pair:revoke-telemetry',
          'pair:revoke-execution'
        }.contains(action)) {
      values['expected_peer_fingerprint'] = selectedPeer!.fingerprint;
      values['expected_peer_revision'] = peerRevision;
    }
    if (engine && action == 'engine:resume') {
      values.remove('config');
      values.remove('role');
    }
    if (engine &&
        action == 'engine:review' &&
        fields['engine_resume'] == true) {
      values['resume'] = true;
      values.remove('config');
      values.remove('role');
    }
    if (!pair && values.containsKey('config')) values.remove('role');
    return values;
  }

  Future<Json> _call(String action,
      {bool review = false, String? confirm}) async {
    final pair = action.startsWith('pair:');
    final engine = action.startsWith('engine:');
    final parameters = _parameters(action);
    if (review && pair) parameters['review'] = true;
    if (confirm != null) parameters['confirm'] = confirm;
    final request = SetupRequest(
        id: 'native-${DateTime.now().microsecondsSinceEpoch}',
        operation: engine
            ? 'installer'
            : pair
                ? 'pair'
                : 'manage',
        selection: const {},
        parameters: parameters);
    final reply = await backend.execute(request);
    if (!reply.ok) throw StateError(reply.error);
    return reply.result;
  }

  Future<void> inspect([String action = 'inspect']) => _run(action, false);
  Future<void> review(String action) => _run(action, true);
  Future<void> _run(String action, bool reviewing) async {
    if (busy ||
        _disposed ||
        (needsInspection &&
            action != 'inspect' &&
            action != 'pair:list' &&
            action != 'engine:status')) {
      return;
    }
    final epoch = ++_epoch;
    final peerSource = fields['link'];
    if (!reviewing && action == 'pair:list') {
      peers = null;
      peerRevision = null;
      selectedPeer = null;
      _peerLink = null;
      result = {};
    }
    busy = true;
    error = '';
    reviewedAction = null;
    _confirmation = null;
    notifyListeners();
    try {
      if (action == 'engine:resume') {
        fields = Map.unmodifiable({...fields, 'engine_resume': true});
      } else if (action == 'engine:install') {
        fields = Map.unmodifiable({...fields, 'engine_resume': false});
      }
      final readAction = reviewing
          ? (action.startsWith('engine:')
              ? 'engine:review'
              : action.startsWith('pair:')
                  ? action
                  : action == 'service-install'
                      ? 'service-plan'
                      : action == 'rollback'
                          ? 'rollback-plan'
                          : action == 'uninstall'
                              ? 'uninstall-plan'
                              : action == 'repair'
                                  ? 'repair-plan'
                                  : action == 'install'
                                      ? 'plan'
                                      : 'inspect')
          : action;
      final value = await _call(readAction, review: reviewing);
      if (_disposed || epoch != _epoch) return;
      if (!reviewing && action == 'pair:list') _setPeers(value, peerSource);
      result = value;
      if (reviewing) {
        final token = value['plan_id'] ??
            value['service_plan_id'] ??
            value['installation_id'];
        if (token is! String || token.isEmpty) {
          throw const FormatException('Missing confirmation identity');
        }
        reviewedAction = action;
        _confirmation = token;
        status = 'Review the exact action and details, then confirm once';
      } else {
        needsInspection = false;
        status = 'Inspection completed; no mutation repeated';
      }
    } catch (_) {
      if (epoch == _epoch) {
        error =
            'Operation failed. Check local inputs, permissions and component availability.';
        status = 'No automatic retry';
      }
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        notifyListeners();
      }
    }
  }

  Future<void> apply() async {
    if (!canApply || _disposed) return;
    final action = reviewedAction!,
        confirmation = _confirmation!,
        epoch = ++_epoch;
    busy = true;
    _mutating = true;
    reviewedAction = null;
    _confirmation = null;
    error = '';
    status = 'Applying reviewed local operation';
    notifyListeners();
    try {
      final value = await _call(action, confirm: confirmation);
      if (_disposed || epoch != _epoch) return;
      result = value;
      status = 'Operation completed. Inspect current state before continuing';
      needsInspection = true;
    } catch (_) {
      if (epoch == _epoch) {
        needsInspection = true;
        error = 'Outcome may be partial. Inspect state before any retry.';
        status = 'Inspection required';
      }
    } finally {
      if (!_disposed && epoch == _epoch) {
        if (action.startsWith('pair:')) {
          peers = null;
          peerRevision = null;
          selectedPeer = null;
          _peerLink = null;
        }
        busy = false;
        _mutating = false;
        notifyListeners();
      }
    }
  }

  Future<void> cancel() async {
    if (!busy || _disposed) return;
    ++_epoch;
    if (_mutating) {
      peers = null;
      peerRevision = null;
      selectedPeer = null;
      _peerLink = null;
    }
    needsInspection = needsInspection || _mutating;
    reviewedAction = null;
    _confirmation = null;
    await backend.cancel();
    if (_disposed) return;
    busy = false;
    _mutating = false;
    status = 'Cancelled; completed effects remain. Inspect before retrying';
    notifyListeners();
  }

  Future<void> shutdown() async {
    await cancel();
    await backend.close();
  }

  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    unawaited(backend.close());
    super.dispose();
  }
}
