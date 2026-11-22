import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// Presentation state for one SSH computer. SSH trust, readiness and monitoring
/// stay in the backend; this controller only sequences user actions.
class SshConnectionController extends ChangeNotifier {
  SshConnectionController(this.api,
      {this.onExecutionChanged, this.beforeExecutionChange});
  final Future<void> Function()? onExecutionChanged;
  final void Function(bool enabled)? beforeExecutionChange;
  Json? execution;
  bool executionNeedsReconciliation = false;

  Future<void> setExecution(bool enabled) async {
    final client = api;
    if (client is! SshExecutionApi ||
        busy ||
        _disposed ||
        executionNeedsReconciliation) {
      return;
    }
    final epoch = _epoch;
    busy = true;
    busyAction = 'execution';
    error = '';
    _notify();
    try {
      beforeExecutionChange?.call(enabled);
      final result = enabled
          ? await (client as SshExecutionApi).enableSshExecution()
          : await (client as SshExecutionApi).disableSshExecution();
      if (_disposed || epoch != _epoch) return;
      execution = result['execution'] is Map
          ? Map<String, dynamic>.from(result['execution'] as Map)
          : null;
      message = enabled
          ? 'SSH command target selected. Each command requires approval. Open Chat and ask for hostname, whoami and pwd.'
          : 'SSH command execution disconnected.';
      await onExecutionChanged?.call();
    } catch (failure) {
      if (!_disposed && epoch == _epoch) _fail(failure);
    } finally {
      if (!_disposed &&
          identical(api, client) &&
          executionNeedsReconciliation) {
        await load();
        if (!_disposed && identical(api, client)) {
          executionNeedsReconciliation = false;
          await onExecutionChanged?.call();
          _notify();
        }
      }
      if (!_disposed && epoch == _epoch) {
        busy = false;
        busyAction = '';
        _notify();
      }
    }
  }

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
    identity = test = target = status = execution = null;
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
      if (client is SshExecutionApi) {
        try {
          final selectedExecution =
              await (client as SshExecutionApi).sshExecutionStatus();
          if (_disposed || epoch != _epoch) return;
          execution = selectedExecution['execution'] is Map
              ? Map<String, dynamic>.from(selectedExecution['execution'] as Map)
              : null;
        } on SshConnectionFailure catch (failure) {
          if (_disposed || epoch != _epoch) return;
          if (failure.status != 404) rethrow;
          execution = null;
        }
      }
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
        if (saved['target']['temporary'] != true) {
          await inspect(savedAlias, manageBusy: false);
        }
        if (_disposed || epoch != _epoch) return;
        await refreshStatus();
        if (_disposed || epoch != _epoch) return;
        _startPolling();
      }
      message = aliases.isEmpty
          ? 'Enter user@hostname directly, or choose an existing SSH alias.'
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

  void editTarget(String value) {
    if (_disposed || busy) return;
    selected = value.trim().isEmpty ? null : value.trim();
    identity = test = null;
    message = error = '';
    _notify();
  }

  Future<void> connect(String address,
      {int? port, String password = ''}) async {
    final client = api;
    if (client == null || busy || _disposed) return;
    if (address.trim().isEmpty) {
      error = message = 'Enter user@hostname.';
      _notify();
      return;
    }
    if (client is! TemporarySshConnectionApi) {
      error = message =
          'Temporary SSH connections require an updated local service.';
      _notify();
      return;
    }
    final epoch = _epoch;
    selected = address.trim();
    identity = test = null;
    busy = true;
    busyAction = 'connect';
    error = '';
    message = 'Connecting to ${selected!}${port == null ? '' : ':$port'}…';
    _notify();
    try {
      final result = await (client as TemporarySshConnectionApi)
          .connectSsh(selected!, port: port, password: password);
      if (_disposed || epoch != _epoch) return;
      identity = result['identity'] is Map
          ? Map<String, dynamic>.from(result['identity'] as Map)
          : null;
      test = result['test'] is Map
          ? Map<String, dynamic>.from(result['test'] as Map)
          : null;
      if (result['connected'] != true || result['target'] is! Map) {
        message =
            '${test?['message'] ?? 'Connection failed. Check the address and credentials.'}';
        error = message;
        return;
      }
      target = result;
      // Reconcile execution in finally for every reconnect outcome.
      status = null;
      ++_statusEpoch;
      statusError = '';
      message =
          'Login verified for this session. Select Use for agent commands below to enable remote operations.';
      await refreshStatus();
      if (!_disposed && epoch == _epoch) _startPolling();
    } catch (failure) {
      if (!_disposed && epoch == _epoch) _fail(failure);
    } finally {
      if (!_disposed && epoch == _epoch && client is SshExecutionApi) {
        final previous = execution;
        try {
          final current =
              await (client as SshExecutionApi).sshExecutionStatus();
          if (!_disposed && epoch == _epoch) {
            execution = current['execution'] is Map
                ? Map<String, dynamic>.from(current['execution'] as Map)
                : null;
          }
        } catch (_) {
          if (!_disposed && epoch == _epoch) execution = null;
        }
        if (!_disposed && epoch == _epoch && !mapEquals(previous, execution)) {
          try {
            await onExecutionChanged?.call();
          } catch (failure) {
            if (error.isEmpty) _fail(failure);
          }
        }
      }
      if (!_disposed && epoch == _epoch) {
        busy = false;
        busyAction = '';
        _notify();
      }
    }
  }

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

  Future<void> testConnection({String password = ''}) async {
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
      if (password.isNotEmpty && client is! SshPasswordConnectionApi) {
        throw const SshConnectionFailure(
            503, 'Password login requires an updated local service.');
      }
      final result = password.isNotEmpty
          ? await (client as SshPasswordConnectionApi)
              .testSshPassword(alias, password)
          : await client.testSshAlias(alias);
      if (_disposed || epoch != _epoch || selected != alias) return;
      test = result['test'] is Map
          ? Map<String, dynamic>.from(result['test'] as Map)
          : null;
      message = test?['ok'] == true
          ? 'SSH login works. Connect, then select Use for agent commands to enable remote operations.'
          : '${test?['message'] ?? 'SSH login did not succeed. Check the username, password or SSH agent.'}';
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

  Future<void> save({String password = ''}) async {
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
      if (password.isNotEmpty && client is! SshPasswordConnectionApi) {
        throw const SshConnectionFailure(
            503, 'Password login requires an updated local service.');
      }
      final result = password.isNotEmpty
          ? await (client as SshPasswordConnectionApi)
              .saveSshPassword(alias, password)
          : await client.saveSshTarget(alias);
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
      final hadExecution = execution != null;
      execution = null;
      if (hadExecution) await onExecutionChanged?.call();
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
    if (action == 'execution') executionNeedsReconciliation = true;
    ++_epoch;
    ++_statusEpoch;
    ++_refreshToken;
    busy = false;
    busyAction = '';
    statusRefreshing = false;
    message = switch (action) {
      'execution' =>
        'Stopped waiting. Execution state will refresh when the service request settles.',
      'connect' =>
        'Stopped waiting. The service may finish connecting; refresh before retrying. No credentials were saved to disk.',
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
    if (failure is StateError) {
      error = message = failure.message;
      return;
    }
    if (failure is SshConnectionFailure && failure.message.isNotEmpty) {
      error = message = failure.message;
      return;
    }
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
