import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';

class CollaborationSettingController extends ChangeNotifier {
  CollaborationSettingController(this.session,
      {this.rounds = false, this.enablement = false}) {
    assert(!(rounds && enablement));
    api = session.api;
    sessionId = session.session?.id;
    session.addListener(_changed);
  }
  final DesktopSession session;
  final bool rounds;
  final bool enablement;
  String get field => enablement
      ? 'enabled'
      : rounds
          ? 'max_rounds'
          : 'max_context_chars';
  late final OdessyApi api;
  late final String? sessionId;
  Json? version;
  bool loading = false, saving = false, disposed = false;
  bool invalidated = false;
  String? error;
  bool get available =>
      !disposed &&
      !invalidated &&
      session.connected &&
      identical(session.api, api) &&
      session.session?.id == sessionId &&
      sessionId != null &&
      (enablement
          ? api is CollaborationEnabledApi
          : rounds
              ? api is RoundLimitApi
              : api is ContextLimitApi) &&
      !api.isDemo;
  void _changed() {
    if (!session.connected ||
        !identical(session.api, api) ||
        session.session?.id != sessionId) {
      invalidated = true;
      version = null;
    }
    if (!disposed) notifyListeners();
  }

  Future<void> load() async {
    if (!available || loading || saving) return;
    version = null;
    error = null;
    loading = true;
    notifyListeners();
    try {
      final result = enablement
          ? await (api as CollaborationEnabledApi).readCollaborationEnabled()
          : rounds
              ? await (api as RoundLimitApi).readRoundLimit()
              : await (api as ContextLimitApi).readContextLimit();
      if (available) version = result;
    } catch (_) {
      if (available) {
        error = 'Could not load the limit. Retry or update the service.';
      }
    } finally {
      loading = false;
      if (!disposed) notifyListeners();
    }
  }

  Future<bool> save(int value) async {
    if (!available ||
        loading ||
        saving ||
        version == null ||
        value < (enablement || rounds ? 0 : 1) ||
        value >
            (enablement
                ? 1
                : rounds
                    ? 32
                    : 100000)) {
      return false;
    }
    final expected = version!;
    version = null;
    saving = true;
    error = null;
    notifyListeners();
    try {
      final update = <String, dynamic>{
        'instance_id': expected['instance_id'],
        'revision': expected['revision'],
        field: enablement ? value == 1 : value
      };
      if (enablement) {
        await (api as CollaborationEnabledApi).saveCollaborationEnabled(update);
      } else if (rounds) {
        await (api as RoundLimitApi).saveRoundLimit(update);
      } else {
        await (api as ContextLimitApi).saveContextLimit(update);
      }
      return available;
    } catch (_) {
      if (available) {
        error = 'Save was not confirmed. Reload the setting before retrying.';
      }
      return false;
    } finally {
      saving = false;
      if (!disposed) notifyListeners();
    }
  }

  @override
  void dispose() {
    disposed = true;
    session.removeListener(_changed);
    super.dispose();
  }
}
