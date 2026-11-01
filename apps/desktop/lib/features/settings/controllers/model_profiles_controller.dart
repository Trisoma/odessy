import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';

class ModelProfilesController extends ChangeNotifier {
  ModelProfilesController(this.session) {
    session.addListener(_bind);
    _bind();
  }
  final DesktopSession session;
  OdessyApi? _api;
  bool _connected = false, _disposed = false;
  int _epoch = 0;
  Json? document;
  bool loading = false, saving = false;
  String? error;
  bool get available =>
      !_disposed && _connected && _api is ModelProfilesApi && !_api!.isDemo;
  bool get canManage => available && _api is ModelProfileManagementApi;
  bool get canSwitch => canManage && _api is ModelProviderSwitchApi;
  bool get canPrice => canManage && _api is ModelProfilePricingApi;
  bool get canReferenceCredentials =>
      canManage && _api is ModelCredentialReferenceApi;
  bool get canSetLimits => canManage && _api is ModelProfileLimitsApi;
  bool _current(int epoch, OdessyApi api) =>
      !_disposed &&
      _epoch == epoch &&
      identical(session.api, api) &&
      session.connected;
  void _bind() {
    final next = session.api, connected = session.connected;
    if (identical(next, _api) && connected == _connected) return;
    _api = next;
    _connected = connected;
    ++_epoch;
    document = null;
    error = null;
    loading = saving = false;
    notifyListeners();
    if (available) unawaited(refresh());
  }

  Future<void> refresh() async {
    if (!available || loading || saving) return;
    final api = _api!, epoch = _epoch;
    document = null;
    loading = true;
    error = null;
    notifyListeners();
    try {
      final data = await (api as ModelProfilesApi).modelProfiles();
      if (_current(epoch, api)) document = data;
    } catch (_) {
      if (_current(epoch, api)) {
        error = 'Model profiles are unavailable. Refresh to retry.';
      }
    } finally {
      if (_current(epoch, api)) {
        loading = false;
        notifyListeners();
      }
    }
  }

  Future<bool> save(
      String name, String model, String endpoint, Json expected) async {
    if (!available ||
        loading ||
        saving ||
        document == null ||
        !identical(document, expected)) {
      return false;
    }
    final api = _api!, epoch = _epoch, version = document!;
    document =
        null; // An unconfirmed mutation must be followed by a fresh read.
    saving = true;
    error = null;
    notifyListeners();
    try {
      final result = await (api as ModelProfilesApi).updateModelProfile(name, {
        'instance_id': version['instance_id'],
        'revision': version['revision'],
        'model': model,
        'endpoint': endpoint,
      });
      if (!_current(epoch, api)) return false;
      document = result;
      return true;
    } catch (_) {
      if (_current(epoch, api)) {
        error =
            'Save failed or settings changed. No success assumed; refresh before retrying.';
      }
      return false;
    } finally {
      if (_current(epoch, api)) {
        saving = false;
        notifyListeners();
      }
    }
  }

  Future<List<Json>?> providers() async {
    if (!canManage || loading || saving || document == null) return null;
    final api = _api!, epoch = _epoch;
    try {
      final data =
          await (api as ModelProfileManagementApi).modelProfileProviders();
      return _current(epoch, api) ? data : null;
    } catch (_) {
      if (_current(epoch, api)) {
        error =
            'Provider catalog is unavailable. Refresh or update the service.';
        notifyListeners();
      }
      return null;
    }
  }

  Future<bool> create(String name, String adapter, String provider,
          String model, String endpoint, Json expected) =>
      _manage(
          (api, version) => api.createModelProfile({
                ...version,
                'name': name,
                'adapter': adapter,
                'provider': provider,
                'model': model,
                'endpoint': endpoint,
              }),
          expected);

  Future<bool> remove(String name, Json expected) => _manage(
      (api, version) => api.deleteModelProfile(name, version), expected);

  Future<bool> switchProvider(String name, String provider, String model,
      String endpoint, Json expected) {
    if (!canSwitch) return Future.value(false);
    return _manage(
        (api, version) => (api as ModelProviderSwitchApi).switchModelProvider(
                name, {
              ...version,
              'provider': provider,
              'model': model,
              'endpoint': endpoint
            }),
        expected);
  }

  Future<Json?> pricing(String name, Json expected) async {
    if (!canPrice || loading || saving || !identical(document, expected)) {
      return null;
    }
    final api = _api!, epoch = _epoch;
    try {
      final data =
          await (api as ModelProfilePricingApi).modelProfilePricing(name);
      if (!_current(epoch, api) ||
          !identical(document, expected) ||
          data['instance_id'] != expected['instance_id'] ||
          data['revision'] != expected['revision']) {
        return null;
      }
      return data;
    } catch (_) {
      if (_current(epoch, api)) {
        error =
            'Profile pricing is unavailable. Refresh or update the service.';
        notifyListeners();
      }
      return null;
    }
  }

  Future<Json?> limits(String name, Json expected) async {
    if (!canSetLimits || loading || saving || !identical(document, expected)) {
      return null;
    }
    final api = _api!, epoch = _epoch;
    try {
      final data =
          await (api as ModelProfileLimitsApi).modelProfileLimits(name);
      if (!_current(epoch, api) ||
          !identical(document, expected) ||
          data['instance_id'] != expected['instance_id'] ||
          data['revision'] != expected['revision']) {
        return null;
      }
      return data;
    } catch (_) {
      if (_current(epoch, api)) {
        error = 'Model limits are unavailable. Refresh or update the service.';
        notifyListeners();
      }
      return null;
    }
  }

  Future<bool> saveLimits(
      String name, int timeout, int maxTokens, Json expected) {
    if (!canSetLimits ||
        timeout < 1 ||
        timeout > 3600 ||
        maxTokens < 1 ||
        maxTokens > 1000000) {
      return Future.value(false);
    }
    return _manage(
        (api, version) => (api as ModelProfileLimitsApi).saveModelProfileLimits(
            name, {...version, 'timeout': timeout, 'max_tokens': maxTokens}),
        expected);
  }

  Future<bool> savePricing(String name, Json? pricing, Json expected) {
    if (!canPrice) return Future.value(false);
    return _manage(
        (api, version) => (api as ModelProfilePricingApi)
            .saveModelProfilePricing(name, {...version, 'pricing': pricing}),
        expected);
  }

  Future<Json?> credentialReference(String name, Json expected) async {
    if (!canReferenceCredentials ||
        loading ||
        saving ||
        !identical(document, expected)) {
      return null;
    }
    final api = _api!, epoch = _epoch;
    try {
      final data = await (api as ModelCredentialReferenceApi)
          .modelCredentialReference(name);
      if (!_current(epoch, api) ||
          !identical(document, expected) ||
          data['instance_id'] != expected['instance_id'] ||
          data['revision'] != expected['revision']) {
        return null;
      }
      return data;
    } catch (_) {
      if (_current(epoch, api)) {
        error =
            'Credential reference is unavailable. Refresh or update the service.';
        notifyListeners();
      }
      return null;
    }
  }

  Future<bool> saveCredentialReference(
      String name, String reference, Json expected) {
    if (!canReferenceCredentials) return Future.value(false);
    return _manage(
        (api, version) => (api as ModelCredentialReferenceApi)
            .saveModelCredentialReference(
                name, {...version, 'credential_env': reference}),
        expected);
  }

  Future<bool> _manage(
      Future<Json> Function(ModelProfileManagementApi, Json) operation,
      Json expected) async {
    if (!canManage ||
        loading ||
        saving ||
        document == null ||
        !identical(document, expected)) {
      return false;
    }
    final api = _api!, epoch = _epoch, current = document!;
    final version = <String, dynamic>{
      'instance_id': current['instance_id'],
      'revision': current['revision']
    };
    document = null;
    saving = true;
    error = null;
    notifyListeners();
    try {
      final response =
          await operation(api as ModelProfileManagementApi, version);
      if (!_current(epoch, api)) return false;
      document = response;
      return true;
    } catch (_) {
      if (_current(epoch, api)) {
        error =
            'Change failed or is unconfirmed. A profile may be referenced by collaboration settings. Refresh before retrying.';
      }
      return false;
    } finally {
      if (_current(epoch, api)) {
        saving = false;
        notifyListeners();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    session.removeListener(_bind);
    super.dispose();
  }
}
