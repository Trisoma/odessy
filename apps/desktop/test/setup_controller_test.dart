import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/setup/controllers/setup_controller.dart';

class Backend implements SetupBackend {
  final requests = <SetupRequest>[];
  Completer<SetupReply>? pending;
  bool cancelled = false;
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    if (pending != null) return pending!.future;
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {
        'plan_id': 'review-id',
        'downloads': [],
        'readiness': 'needs_configuration',
        'selection': request.selection,
        'recovery': 'matches',
        'saved': true,
      }
    }, request.id);
  }

  @override
  Future<void> cancel() async {
    cancelled = true;
  }

  @override
  Future<void> close() async {}
}

void main() {
  test('discovery works before choosing a model name', () async {
    final backend = Backend();
    final controller = SetupController(backend);
    controller.update({
      'role': 'combined',
      'model': {'provider': 'ollama', 'name': ''}
    }, '/tmp/config.json');
    await controller.discover('http://127.0.0.1:11434');
    expect(backend.requests.single.selection, {'role': 'combined'});
    controller.dispose();
  });

  test('save requires review and edits invalidate it', () async {
    final backend = Backend();
    final controller = SetupController(backend);
    controller.update({'role': 'combined'}, '/tmp/selection.json');
    await controller.save();
    expect(backend.requests, isEmpty);
    await controller.review();
    expect(controller.canSave, isTrue);
    controller.update({'role': 'coordinator'}, '/tmp/selection.json');
    expect(controller.canSave, isFalse);
    await controller.save();
    expect(backend.requests.length, 1);
    controller.dispose();
  });
  test('cancel suppresses stale completion and recovery never repeats save',
      () async {
    final backend = Backend()..pending = Completer<SetupReply>();
    final controller = SetupController(backend);
    controller.update({'role': 'combined'}, '/tmp/recover.json');
    final operation = controller.review();
    await controller.cancel();
    final request = backend.requests.single;
    backend.pending!.complete(SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {'plan_id': 'stale'}
    }, request.id));
    await operation;
    expect(controller.canSave, isFalse);
    expect(backend.cancelled, isTrue);
    backend.pending = null;
    await controller.recover();
    expect(backend.requests.last.operation, 'recover');
    expect(backend.requests.where((request) => request.operation == 'save'),
        isEmpty);
    controller.dispose();
  });
  test('cancelled save pins its recovery selection and destination', () async {
    final backend = Backend();
    final controller = SetupController(backend);
    controller.update({'role': 'combined'}, '/tmp/original.json');
    await controller.review();
    backend.pending = Completer<SetupReply>();
    final operation = controller.save();
    await controller.cancel();
    expect(controller.recoveryRequired, isTrue);
    controller.update({'role': 'execution'}, '/tmp/changed.json');
    expect(controller.output, '/tmp/original.json');
    final request = backend.requests.last;
    backend.pending!.complete(SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {'saved': true}
    }, request.id));
    await operation;
    expect(controller.saved, isFalse);
    backend.pending = null;
    await controller.recover();
    expect(backend.requests.last.output, '/tmp/original.json');
    expect(backend.requests.last.selection['role'], 'combined');
    controller.dispose();
  });
}
