import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// Presentation state for one SSH computer. SSH trust, readiness and monitoring
/// stay in the backend; this controller only sequences user actions.
class SshConnectionController extends ChangeNotifier {
  SshConnectionController(this.api);
  SshConnectionApi? api;
  List<String> aliases = const [];
  String? selected;
  Json? identity, test, target, status;
  bool busy = false, removing = false, statusRefreshing = false;
  String busyAction = '';
  String message = '', error = '', statusError = '';
  Timer? _poll;
  bool _disposed = false;
  int _epoch = 0;
  int _statusEpoch = 0;
  int _refreshToken = 0;

  bool get hasTarget => target?['target'] is Map;
  String? get targetHostId {
    final value = target?['target'];
    if (value is Map) {
      final id = value['host_id'];
      return id is String && id.isNotEmpty ? id : null;
    }
    return null;
  }

  bool get known => identity?['known'] == true;
  bool get changed => identity?['changed'] == true;
  String? get selectedFingerprint {
    final values = identity?['known_fingerprints'];
    if (values is List && values.isNotEmpty) return '${values.first}';
    final discovered = identity?['discovered_fingerprints'];
    if (discovered is List && discovered.isNotEmpty) {
      return '${discovered.first}';
    }
    return null;
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  void bind(SshConnectionApi? value) {
    if (identical(api, value)) return;
    api = value;
    ++_epoch;
    ++_statusEpoch;
    _poll?.cancel();
    _poll = null;
    aliases = const [];
    selected = null;
    identity = test = target = status = null;
    busy = removing = statusRefreshing = false;
    busyAction = '';
    message = '';
    error = '';
    statusError = '';
    _notify();
    if (value != null) unawaited(load());
  }

  Future<void> load() async {
    final client = api;
    if (client == null || _disposed) return;
    final epoch = _epoch;
    busy = true;
    busyAction = 'load';
    _notify();
    try {
      final saved = await client.sshTarget();
      if (_disposed || epoch != _epoch) return;
      final previousHost = targetHostId;
      target = saved;
      if (previousHost != targetHostId) status = null;
      ++_statusEpoch;
      statusError = '';
      final aliasesResult = await client.sshAliases();
      if (_disposed || epoch != _epoch) return;
      aliases = ((aliasesResult['aliases'] as List?) ?? const [])
          .whereType<Map>()
          .map((value) => '${value['alias']}')
          .where((value) => value.isNotEmpty)
          .toList(growable: false);
      final savedAlias = (saved['target'] is Map)
          ? '${(saved['target'] as Map)['alias']}'
          : null;
      if (savedAlias != null && savedAlias.isNotEmpty) {
        if (selected != savedAlias) identity = test = null;
        selected = savedAlias;
        await inspect(savedAlias, manageBusy: false);
        if (_disposed || epoch != _epoch) return;
        await refreshStatus();
        if (_disposed || epoch != _epoch) return;
        _startPolling();
      } else if (aliases.isNotEmpty &&
          (selected == null || !aliases.contains(selected))) {
        identity = test = null;
        selected = aliases.first;
        await inspect(selected!, manageBusy: false);
      }
      message = aliases.isEmpty
          ? 'No SSH aliases found. Add a Host entry to your OpenSSH config, then refresh.'
          : '';
    } catch (failure) {
      if (_disposed || epoch != _epoch) return;
      _fail(failure);
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        busyAction = '';
        _notify();
      }
    }
  }

  Future<void> refreshAliases() => load();

  Future<void> select(String alias) async {
    if (busy || _disposed) return;
    selected = alias;
    identity = test = null;
    error = '';
    message = '';
    _notify();
    await inspect(alias);
  }

  Future<void> inspect(String alias, {bool manageBusy = true}) async {
    final client = api;
    if (client == null || _disposed) return;
    final epoch = _epoch;
    if (manageBusy) {
      busy = true;
      busyAction = 'inspect';
    }
    _notify();
    try {
      final result = await client.inspectSshAlias(alias);
      if (_disposed || epoch != _epoch || selected != alias) return;
      identity = result['identity'] is Map
          ? Map<String, dynamic>.from(result['identity'] as Map)
          : null;
      test = null;
      final changedValue = identity?['changed'] == true;
      final knownValue = identity?['known'] == true;
      message = changedValue
          ? 'Host key changed. Do not continue until you verify the new fingerprint out of band.'
          : knownValue
              ? 'Host key is already trusted in your OpenSSH known_hosts.'
              : 'Host key is not trusted yet. Review the discovered fingerprint independently.';
      error = '';
    } catch (failure) {
      if (_disposed || epoch != _epoch || selected != alias) return;
      _fail(failure);
    } finally {
      if (manageBusy && !_disposed && epoch == _epoch) {
        busy = false;
        busyAction = '';
        _notify();
      }
    }
  }

  Future<void> testConnection() async {
    final client = api;
    final alias = selected;
    if (client == null || alias == null || busy || _disposed) return;
    final epoch = _epoch;
    busy = true;
    busyAction = 'test';
    error = '';
    message = 'Testing the read-only SSH connection…';
    _notify();
    try {
      final result = await client.testSshAlias(alias);
      if (_disposed || epoch != _epoch || selected != alias) return;
      test = result['test'] is Map
          ? Map<String, dynamic>.from(result['test'] as Map)
          : null;
      message = test?['ok'] == true
          ? 'SSH login works. Only read-only status is available in this build.'
          : 'SSH login did not succeed. Check the alias, key/agent and host key.';
    } catch (failure) {
      if (_disposed || epoch != _epoch || selected != alias) return;
      _fail(failure);
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        busyAction = '';
        _notify();
      }
    }
  }

  Future<void> save() async {
    final client = api;
    final alias = selected;
    if (client == null || alias == null || busy || _disposed) return;
    final epoch = _epoch;
    busy = true;
    busyAction = 'save';
    removing = false;
    error = '';
    message = 'Saving the SSH computer…';
    _notify();
    try {
      final result = await client.saveSshTarget(alias);
      if (_disposed || epoch != _epoch || selected != alias) return;
      final previousHost = targetHostId;
      target = result;
      if (previousHost != targetHostId) status = null;
      ++_statusEpoch;
      statusError = '';
      test = result['test'] is Map
          ? Map<String, dynamic>.from(result['test'] as Map)
          : test;
      message =
          'Saved. Monitoring will appear under Devices when the service is ready.';
      await refreshStatus();
      if (_disposed || epoch != _epoch) return;
      _startPolling();
    } catch (failure) {
      if (_disposed || epoch != _epoch || selected != alias) return;
      _fail(failure);
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        busyAction = '';
        _notify();
      }
    }
  }

  Future<void> remove() async {
    final client = api;
    if (client == null || busy || _disposed) return;
    final epoch = _epoch;
    busy = true;
    busyAction = 'remove';
    removing = true;
    ++_statusEpoch;
    error = '';
    statusError = '';
    _notify();
    try {
      final result = await client.removeSshTarget();
      if (_disposed || epoch != _epoch) return;
      target = null;
      test = null;
      status = null;
      ++_statusEpoch;
      _poll?.cancel();
      _poll = null;
      message = '${result['message'] ?? 'Removed from Odessy.'}';
    } catch (failure) {
      if (_disposed || epoch != _epoch) return;
      _fail(failure);
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        busyAction = '';
        removing = false;
        _notify();
      }
    }
  }

  Future<void> refreshStatus() async {
    final client = api;
    if (client == null || _disposed || !hasTarget || statusRefreshing) return;
    final epoch = _epoch;
    final statusEpoch = _statusEpoch;
    final hostId = targetHostId;
    final refreshToken = ++_refreshToken;
    statusRefreshing = true;
    _notify();
    try {
      final result = await client.sshComputer();
      if (_disposed ||
          epoch != _epoch ||
          statusEpoch != _statusEpoch ||
          hostId == null ||
          hostId != targetHostId) {
        return;
      }
      final computer = result['computer'];
      if (computer is! Map) {
        statusError = 'Status response is invalid.';
        return;
      }
      status = Map<String, dynamic>.from(computer);
      statusError = '';
    } catch (failure) {
      if (_disposed ||
          epoch != _epoch ||
          statusEpoch != _statusEpoch ||
          hostId == null ||
          hostId != targetHostId) {
        return;
      }
      statusError = _statusMessage(failure);
      final previous = status;
      if (previous != null) {
        final stale = Map<String, dynamic>.from(previous);
        stale['readiness'] = 'unavailable';
        final oldStatus = previous['status'];
        final state = oldStatus is Map
            ? Map<String, dynamic>.from(oldStatus)
            : <String, dynamic>{};
        state['connectivity'] = 'unknown';
        state['error'] = statusError;
        stale['status'] = state;
        status = stale;
      }
    } finally {
      if (!_disposed && refreshToken == _refreshToken) {
        statusRefreshing = false;
        _notify();
      }
    }
  }

  String _statusMessage(Object failure) {
    final status = failure is SshConnectionFailure ? failure.status : 0;
    return switch (status) {
      404 => 'Status unavailable: the saved SSH computer is no longer present.',
      503 => 'Status unavailable: the monitoring service is not running.',
      _ => 'Status unavailable: the local service did not answer.',
    };
  }

  void cancel() {
    if (!busy) return;
    final action = busyAction;
    ++_epoch;
    ++_statusEpoch;
    ++_refreshToken;
    busy = false;
    busyAction = '';
    statusRefreshing = false;
    message = switch (action) {
      'save' =>
        'Stopped waiting. The service may still finish saving the SSH computer; refresh to see the current target.',
      'remove' =>
        'Stopped waiting. The service may still finish removing the SSH computer; refresh to see the current target.',
      _ =>
        'Stopped waiting. The bounded check can still finish within 25 s; cancelling changed nothing remote.',
    };
    _notify();
  }

  void _startPolling() {
    _poll?.cancel();
    _poll = Timer.periodic(const Duration(seconds: 5), (_) {
      if (!_disposed && hasTarget) unawaited(refreshStatus());
    });
  }

  void _fail(Object failure) {
    final status = failure is SshConnectionFailure ? failure.status : 0;
    error = switch (status) {
      409 =>
        'SSH login or host identity is not accepted. Check the alias, key/agent and fingerprint review, then retry.',
      503 =>
        'The SSH service is unavailable. Check that OpenSSH is installed and the monitoring service is running.',
      400 =>
        'The SSH alias or configuration could not be used. Check the resolved host and user.',
      _ => 'The SSH operation failed. Check the alias, key/agent and retry.',
    };
    message = error;
  }

  @override
  void dispose() {
    _disposed = true;
    _epoch++;
    _poll?.cancel();
    super.dispose();
  }
}
